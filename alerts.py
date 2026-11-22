"""
Alertes : bonnes affaires et prix cibles, envoyées sur Discord et/ou par e-mail.

- Bonne affaire : une annonce au moins X % sous la valeur de marché des 7 derniers jours,
  sur un objet qui se vend (au moins 1 par jour), avec un gain potentiel suffisant.
- Prix cibles : définis dans alerts.json (« below » = acheter sous ce prix,
  « above » = le prix le plus bas de l'HV dépasse ce montant, bon moment pour vendre).

Canaux (secrets GitHub, tous facultatifs) :
  DISCORD_WEBHOOK                        adresse du webhook Discord
  SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL  e-mail (Gmail par défaut, mot de passe d'application)
  SMTP_HOST, SMTP_PORT                   autre serveur que Gmail (facultatif)
"""
import json
import os
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

MAX_ALERTS_PER_RUN = 15


def parse_money(v):
    """'12po 50pa', '12.5' (po) ou un nombre de cuivre -> cuivre."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).lower().replace(",", ".").strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return round(float(s) * 10000)
    total = 0
    for amount, unit in re.findall(r"(\d+)\s*(po|pa|pc|g|s|c)", s):
        total += int(amount) * {"po": 10000, "g": 10000, "pa": 100, "s": 100, "pc": 1, "c": 1}[unit]
    return total or None


def fmt(c):
    if c is None:
        return "—"
    c = round(c)
    g, s, k = c // 10000, c % 10000 // 100, c % 100
    parts = []
    if g:
        parts.append(f"{g:,}".replace(",", " ") + "po")
    if s or g:
        parts.append(f"{s}pa")
    if not g:
        parts.append(f"{k}pc")
    return " ".join(parts)


def find_alerts(current, items, rules, cfg, sent, now_ts):
    """-> liste d'alertes {key, id, kind, price, title, lines, gain}"""
    out = []
    cut = cfg.get("ah_cut", 0.05)
    a = cfg.get("alerts", {})
    pct = a.get("deal_pct", 40)
    min_value = parse_money(a.get("min_value", "1po"))
    min_profit = parse_money(a.get("min_profit", "5po"))
    repeat = a.get("repeat_hours", 12) * 3600

    def fresh(key, price):
        last = sent.get(key)
        if not last:
            return True
        ts, old_price = last
        return now_ts - ts >= repeat or (price and old_price and price <= old_price * 0.9)

    name = lambda i: (items.get(str(i)) or {}).get("n", f"Objet #{i}")

    # bonnes affaires automatiques
    if a.get("deals", True):
        for k, d in current.items():
            m7, p = d.get("m7"), d.get("p")
            if not m7 or not p or d.get("h", 0) < 2 or (d.get("s7") or 0) < 1 or m7 < min_value:
                continue
            if p > m7 * (1 - pct / 100):
                continue
            units = min(d.get("pq", 1), max(1, round(d["s7"])))
            gain = (m7 * (1 - cut) - p) * units
            if gain < min_profit:
                continue
            key = f"deal:{k}"
            if not fresh(key, p):
                continue
            out.append({
                "key": key, "id": k, "kind": "deal", "price": p, "gain": gain,
                "title": f"Bonne affaire : {name(k)}",
                "lines": [
                    f"Prix : **{fmt(p)}** au lieu de {fmt(m7)} (−{round((1 - p / m7) * 100)} %)",
                    f"Disponible à ce prix : {d.get('pq', 1)} · se vend ~{d['s7']:g} / jour",
                    f"Gain potentiel en revendant au prix normal : ~{fmt(gain)}",
                ],
            })

    # prix cibles de alerts.json
    for r in rules:
        k = str(r.get("id"))
        d = current.get(k)
        if not d:
            continue
        below, above = parse_money(r.get("below")), parse_money(r.get("above"))
        note = f" — {r['note']}" if r.get("note") else ""
        if below and d["p"] <= below and fresh(f"below:{k}", d["p"]):
            out.append({
                "key": f"below:{k}", "id": k, "kind": "below", "price": d["p"], "gain": 1e12,
                "title": f"Prix cible atteint : {name(k)}",
                "lines": [f"Prix le plus bas : **{fmt(d['p'])}** (votre cible : {fmt(below)}){note}",
                          f"Disponible à ce prix : {d.get('pq', 1)} · valeur de marché {fmt(d.get('v'))}"],
            })
        if above and d["p"] >= above and fresh(f"above:{k}", None):
            out.append({
                "key": f"above:{k}", "id": k, "kind": "above", "price": d["p"], "gain": 1e12,
                "title": f"Bon moment pour vendre : {name(k)}",
                "lines": [f"Prix le plus bas à l'HV : **{fmt(d['p'])}** (votre seuil : {fmt(above)}){note}",
                          f"Valeur de marché {fmt(d.get('v'))} · se vend ~{d.get('s7') or '?'} / jour"],
            })

    out.sort(key=lambda x: -x["gain"])
    return out[:MAX_ALERTS_PER_RUN]


def send_discord(webhook, alerts, items, label, site):
    colors = {"deal": 0x4FC17B, "below": 0xE0B44C, "above": 0x6C8CFF}
    embeds = []
    for al in alerts:
        e = {"title": al["title"], "description": "\n".join(al["lines"]), "color": colors[al["kind"]],
             "footer": {"text": f"HV {label}"}}
        if site:
            e["url"] = site
        icon = (items.get(al["id"]) or {}).get("i")
        if icon:
            e["thumbnail"] = {"url": icon}
        embeds.append(e)
    for i in range(0, len(embeds), 10):
        r = requests.post(webhook, json={"username": "Mon HV", "embeds": embeds[i:i + 10]}, timeout=30)
        r.raise_for_status()


def send_email(alerts, label, site):
    user, password, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD"), os.environ.get("ALERT_EMAIL")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    clean = lambda s: s.replace("**", "")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Mon HV : {len(alerts)} alerte{'s' if len(alerts) > 1 else ''} ({alerts[0]['title']})"
    msg["From"], msg["To"] = user, to
    text = "\n\n".join(clean(a["title"]) + "\n" + "\n".join(clean(l) for l in a["lines"]) for a in alerts)
    bold = lambda l: re.sub(r"[*][*](.+?)[*][*]", r"<b>\1</b>", l)
    html = "".join("<h3 style='margin:16px 0 4px'>" + a["title"] + "</h3>" +
                   "".join("<div>" + bold(l) + "</div>" for l in a["lines"])
                   for a in alerts)
    if site:
        text += f"\n\nTableau de bord : {site}"
        html += f"<p><a href='{site}'>Ouvrir le tableau de bord</a></p>"
    msg.attach(MIMEText(text + f"\n\nHV {label}", "plain", "utf-8"))
    msg.attach(MIMEText(f"<div style='font-family:sans-serif'>{html}<p style='color:#888'>HV {label}</p></div>", "html", "utf-8"))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())


def process(current, items, rules, cfg, sent, now_ts, label):
    """Calcule, envoie et met à jour l'historique des envois. Renvoie le nombre d'alertes envoyées."""
    alerts = find_alerts(current, items, rules, cfg, sent, now_ts)
    if not alerts:
        return 0
    site = cfg.get("alerts", {}).get("site_url")
    webhook = os.environ.get("DISCORD_WEBHOOK")
    has_mail = all(os.environ.get(v) for v in ("SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL"))
    if not webhook and not has_mail:
        print(f"  {len(alerts)} alerte(s) trouvée(s), mais aucun canal configuré (Discord ou e-mail)")
        return 0
    delivered = False
    if webhook:
        try:
            send_discord(webhook, alerts, items, label, site)
            delivered = True
        except Exception as e:
            print(f"  ! Discord : {e}")
    if has_mail:
        try:
            send_email(alerts, label, site)
            delivered = True
        except Exception as e:
            print(f"  ! E-mail : {e}")
    if delivered:
        for a in alerts:
            sent[a["key"]] = [now_ts, a["price"]]
        print(f"  {len(alerts)} alerte(s) envoyée(s)")
        return len(alerts)
    return 0
