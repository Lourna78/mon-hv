#!/usr/bin/env python3
"""
Collecteur HV — World of Warcraft: Forever (EU)

Télécharge chaque heure les annonces de l'hôtel des ventes via l'API Blizzard,
calcule les prix (plus bas, médiane, quantités), estime les ventes en comparant
deux photos successives, et écrit des fichiers JSON lus par le tableau de bord.

Usage :
  python collector.py                 # collecte normale
  python collector.py --discover      # cherche quels numéros d'HV renvoient des annonces

Variables d'environnement requises : BNET_CLIENT_ID, BNET_CLIENT_SECRET
"""
import argparse
import gzip
import json
import os
import shutil
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

SITE = Path(os.environ.get("SITE_DIR", ROOT / "site"))
PUBLIC = SITE / "public"
DATA = PUBLIC / "data"
STATE = SITE / "state"

SHARDS = 50                 # l'historique journalier est réparti en 50 fichiers
MAX_NEW_ITEMS_PER_RUN = 8000  # limite d'appels fiche/icône par passage (Blizzard : 36 000/heure)
LONG_TIME_LEFT = {"LONG", "VERY_LONG"}  # annonce qui avait encore >2 h devant elle


# --------------------------------------------------------------------------- API
class Api:
    def __init__(self):
        cid = os.environ.get("BNET_CLIENT_ID")
        secret = os.environ.get("BNET_CLIENT_SECRET")
        if not cid or not secret:
            sys.exit("BNET_CLIENT_ID / BNET_CLIENT_SECRET manquants.")
        r = requests.post(
            "https://oauth.battle.net/token",
            data={"grant_type": "client_credentials"},
            auth=(cid, secret),
            timeout=30,
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]
        self.base = f"https://{CONFIG['region']}.api.blizzard.com"
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {self.token}"

    def get(self, path, namespace, retries=6, extra=None, locale=True):
        """Renvoie (statut, json ou None, en-tête Last-Modified)."""
        params = {"namespace": namespace}
        if locale:
            params["locale"] = CONFIG["locale"]
        params.update(extra or {})
        for attempt in range(retries):
            throttle()
            try:
                r = self.s.get(self.base + path, params=params, timeout=120)
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):  # Blizzard demande de ralentir
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                return r.status_code, None, None
            return 200, r.json(), r.headers.get("Last-Modified")
        return 0, None, None


# ------------------------------------------------------------------ utilitaires
_lock = threading.Lock()
_next_slot = [0.0]
MAX_RPS = 60  # Blizzard autorise 100 requêtes/s : on reste en dessous


def throttle():
    """Espace les appels pour ne jamais dépasser MAX_RPS."""
    with _lock:
        now = time.monotonic()
        wait = _next_slot[0] - now
        _next_slot[0] = max(now, _next_slot[0]) + 1 / MAX_RPS
    if wait > 0:
        time.sleep(wait)


def load(path, default):
    path = Path(path)
    if not path.exists():
        return default
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
    else:
        path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def weighted_median(pairs):
    """pairs = [(prix_unitaire, quantité)] -> médiane pondérée par la quantité."""
    pairs = sorted(pairs)
    total = sum(q for _, q in pairs)
    half, acc = total / 2, 0
    for price, q in pairs:
        acc += q
        if acc >= half:
            return price
    return pairs[-1][0]


def market_value(pairs):
    """Valeur de marché : moyenne des 15 % d'unités les moins chères, sans les prix aberrants.

    Une annonce isolée à 1 pc (erreur, troll) ou une poignée d'unités hors de prix
    ne déplace plus la valeur, contrairement au simple « prix le plus bas ».
    """
    pairs = sorted(pairs)
    total = sum(q for _, q in pairs)
    if total < 4:
        return weighted_median(pairs)
    want = max(int(total * 0.15), min(total, 4))
    take, acc = [], 0
    for price, q in pairs:
        n = min(q, want - acc)
        take.append((price, n))
        acc += n
        if acc >= want:
            break
    mid = weighted_median(take)
    kept = [(p, q) for p, q in take if mid * 0.5 <= p <= mid * 1.5] or take
    return round(sum(p * q for p, q in kept) / sum(q for _, q in kept))


def unit_price(a):
    if a.get("unit_price"):
        return a["unit_price"]
    if a.get("buyout"):
        return a["buyout"] // max(a.get("quantity", 1), 1)
    return None  # enchère seule : ignorée pour les prix


# ------------------------------------------------------------------- collecte
def fetch_house(api, ah_id):
    """Annonces + matières premières (commodities) d'un HV. Renvoie (liste, last_modified)."""
    rs = CONFIG["ruleset_id"]
    ns = CONFIG["namespace"]
    auctions, last_mod = [], None
    for suffix in ("", "/commodities"):
        status, js, lm = api.get(f"/data/wow/ruleset/{rs}/auctions/{ah_id}{suffix}", ns)
        if status == 200 and js:
            auctions.extend(js.get("auctions") or [])
            last_mod = last_mod or lm
        elif suffix == "":
            print(f"  ! HV {ah_id} : statut {status}")
    return auctions, last_mod


def summarize(auctions):
    """-> {item_id: {p, pq, v, m, q, n}} et l'instantané compact pour la détection de ventes.

    p = prix le plus bas, pq = unités disponibles à ce prix, v = valeur de marché,
    m = médiane pondérée, q = unités en vente, n = annonces."""
    by_item, snap = {}, {}
    for a in auctions:
        item = a.get("item", {}).get("id")
        if not item:
            continue
        qty = a.get("quantity", 1) or 1
        up = unit_price(a)
        snap[str(a["id"])] = [item, qty, a.get("time_left", ""), up or 0]
        if up:
            by_item.setdefault(item, []).append((up, qty))
    out = {}
    for item, pairs in by_item.items():
        low = min(p for p, _ in pairs)
        out[item] = {
            "p": low,
            "pq": sum(q for p, q in pairs if p == low),
            "v": market_value(pairs),
            "m": weighted_median(pairs),
            "q": sum(q for _, q in pairs),
            "n": len(pairs),
        }
    return out, snap


def estimate_sales(prev_snap, snap, summary):
    """Unités probablement vendues entre deux photos.

    - une annonce dont la quantité a baissé : achat partiel, compté ;
    - une annonce disparue alors qu'il lui restait plus de 2 h, et qui faisait partie des moins chères
      (≤ 5 % au-dessus du prix le plus bas d'avant, ou ≤ prix le plus bas actuel, ou l'objet n'est
      plus en vente du tout) : achat, compté ;
    - une annonce disparue mais plus chère que ce qui reste en vente : le vendeur l'a sûrement
      retirée pour la remettre moins cher (sous-enchère) : pas comptée.
    """
    prev_low = {}
    for entry in prev_snap.values():
        if len(entry) > 3 and entry[3]:
            prev_low[entry[0]] = min(prev_low.get(entry[0], entry[3]), entry[3])
    sold = {}
    for aid, entry in prev_snap.items():
        item, qty, tl = entry[0], entry[1], entry[2]
        price = entry[3] if len(entry) > 3 else None
        cur = snap.get(aid)
        if cur is None:
            if tl not in LONG_TIME_LEFT or not price:
                continue
            low_now = (summary.get(item) or {}).get("p")
            if low_now is None or price <= max(low_now, prev_low.get(item, 0) * 1.05):
                sold[item] = sold.get(item, 0) + qty
        elif cur[1] < qty:
            sold[item] = sold.get(item, 0) + (qty - cur[1])
    return sold


def loc(v, default=""):
    """Les recherches Blizzard renvoient les noms dans toutes les langues : on garde le français."""
    if isinstance(v, dict):
        return v.get(CONFIG["locale"]) or v.get("en_GB") or v.get("en_US") or next(iter(v.values()), default)
    return v or default


def item_entry(data, old=None):
    old = old or {}
    return {
        "n": loc(data.get("name")) or old.get("n") or f"Objet #{data.get('id')}",
        "c": loc((data.get("item_class") or {}).get("name")) or old.get("c", "Autre"),
        "sc": loc((data.get("item_subclass") or {}).get("name")) or old.get("sc", ""),
        "ql": (data.get("quality") or {}).get("type") or old.get("ql", "COMMON"),
        **({"i": old["i"]} if "i" in old else {}),
        **({"ni": old["ni"]} if "ni" in old else {}),
    }


def refresh_catalog(api, items, meta, now):
    """Catalogue complet du jeu (objets, ressources, patrons...) via la recherche d'objets, une fois par jour."""
    if now.timestamp() - meta.get("catalog_ts", 0) < 24 * 3600:
        return False
    namespaces = [CONFIG["static_namespace"], f"static-forever-{CONFIG['region']}"]
    for ns in namespaces:
        last, pages, added = 0, 0, 0
        while pages < 200:
            status, js, _ = api.get("/data/wow/search/item", ns, locale=False, extra={
                "orderby": "id", "_pageSize": 1000, "id": f"[{last + 1},]"})
            if status != 200 or not js:
                break
            results = js.get("results") or []
            if not results:
                break
            for r in results:
                d = r.get("data") or {}
                if not d.get("id"):
                    continue
                k = str(d["id"])
                if k not in items:
                    added += 1
                items[k] = item_entry(d, items.get(k))
                last = max(last, d["id"])
            pages += 1
        if pages:
            print(f"  Catalogue : {len(items)} objets ({added} nouveaux, {pages} pages)")
            meta["catalog_ts"] = int(now.timestamp())
            return True
        print(f"  Catalogue indisponible avec {ns} (statut {status})")
    return False


def update_items(api, item_ids, meta, now):
    """Noms, catégories et icônes : catalogue complet + fiches et icônes manquantes, en priorité les objets en vente."""
    items = load(DATA / "items.json", {})
    refresh_catalog(api, items, meta, now)
    namespaces = [CONFIG["static_namespace"], f"static-forever-{CONFIG['region']}"]

    priority = [str(i) for i in item_ids]
    pset = set(priority)
    # 1) objets en vente absents du catalogue (si la recherche a échoué)
    tries = meta.setdefault("unknown_tries", {})
    unknown = [k for k in priority if k not in items and tries.get(k, 0) < 3][:MAX_NEW_ITEMS_PER_RUN]
    # 2) icônes manquantes : objets en vente d'abord, puis tout le reste du catalogue
    def needs_icon(k):
        e = items.get(k)
        return e is not None and not e.get("i") and e.get("ni", 0) < 3
    icons = [k for k in priority if needs_icon(k)] + [k for k in items if k not in pset and needs_icon(k)]
    budget = max(0, MAX_NEW_ITEMS_PER_RUN - len(unknown))
    icons = icons[:budget]

    def fetch_item(k):
        for ns in namespaces:
            st, js, _ = api.get(f"/data/wow/item/{k}", ns)
            if st == 200 and js:
                return k, item_entry(js)
        return k, None

    def fetch_icon(k):
        for ns in namespaces:
            st, media, _ = api.get(f"/data/wow/media/item/{k}", ns)
            if st == 200 and media:
                for asset in media.get("assets", []):
                    if asset.get("key") == "icon" and asset.get("value"):
                        return k, asset["value"]
                return k, None
            if st == 404:
                continue
        return k, None

    if unknown:
        print(f"  Fiches objets à récupérer : {len(unknown)}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            for k, info in pool.map(fetch_item, unknown):
                if not info:
                    tries[k] = tries.get(k, 0) + 1
                if info:
                    tries.pop(k, None)
                    items[k] = info
                    if len(icons) < MAX_NEW_ITEMS_PER_RUN and needs_icon(k):
                        icons.insert(0, k)
    if icons:
        got = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for k, icon in pool.map(fetch_icon, icons):
                if icon:
                    items[k]["i"] = icon
                    items[k].pop("ni", None)
                    got += 1
                else:
                    items[k]["ni"] = items[k].get("ni", 0) + 1
        print(f"  Icônes récupérées : {got} / {len(icons)}")
    left = sum(1 for k in items if needs_icon(k))
    print(f"  Objets connus : {len(items)} — icônes encore à récupérer : {left}")
    save(DATA / "items.json", items)
    return items


def update_history(house, summary, sold, now):
    """Historique journalier par objet : [date, plus_bas, valeur_marché_moy, qté_moy, vendus, heures]."""
    today = now.strftime("%Y-%m-%d")
    keep_from = (now - timedelta(days=CONFIG["history_days"])).strftime("%Y-%m-%d")
    hdir = DATA / house / "history"
    shards = {s: load(hdir / f"{s}.json", {}) for s in range(SHARDS)}

    touched = set(summary) | set(sold)
    for item in touched:
        sh = shards[int(item) % SHARDS]
        rows = sh.setdefault(str(item), [])
        s = summary.get(item)
        if rows and rows[-1][0] == today:
            d, low, med, qty, sld, hrs = rows[-1]
            if s:
                low = s["p"] if low is None else min(low, s["p"])
                med = s["v"] if med is None else round((med * hrs + s["v"]) / (hrs + 1))
                qty = round((qty * hrs + s["q"]) / (hrs + 1))
                hrs += 1
            sld += sold.get(item, 0)
            rows[-1] = [d, low, med, qty, sld, hrs]
        else:
            rows.append([today, s["p"] if s else None, s["v"] if s else None,
                         s["q"] if s else 0, sold.get(item, 0), 1 if s else 0])

    for sh in shards.values():
        for k in list(sh):
            sh[k] = [r for r in sh[k] if r[0] >= keep_from]
            if not sh[k]:
                del sh[k]
    for s, sh in shards.items():
        save(hdir / f"{s}.json", sh)
    return shards


def pct(a, b):
    return None if not a or not b else round((a - b) / b * 100, 1)


def build_current(summary, shards, now):
    """Fichier léger lu par toutes les vues : prix actuels + indicateurs."""
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    out = {}
    for item, s in summary.items():
        rows = shards[int(item) % SHARDS].get(str(item), [])
        by_date = {r[0]: r for r in rows}
        last7 = [r for r in rows if r[0] > week_ago]
        meds = [r[2] for r in last7 if r[2]]
        full_days = [r for r in last7 if r[0] != today and r[5] >= 12]
        sold_days = full_days or [r for r in last7 if r[5]]
        hours = sum(r[5] for r in sold_days)
        s7 = round(sum(r[4] for r in sold_days) / hours * 24, 1) if hours >= 6 else None
        y = by_date.get(yesterday)
        w = by_date.get(week_ago) or (rows[0] if rows and rows[0][0] < yesterday else None)
        out[item] = {
            **s,
            "m7": round(statistics.median(meds)) if meds else s["v"],   # valeur de marché médiane sur 7 jours
            "d1": pct(s["v"], y[2] if y else None),                    # valeur de marché vs hier
            "w1": pct(s["v"], w[2] if w else None),                    # valeur de marché vs il y a 7 jours
            "s7": s7,
            "h": len(rows),                                            # jours d'historique
        }
    return out


# ------------------------------------------------------------------ programme
def run():
    api = Api()
    now = datetime.now(timezone.utc)
    meta = load(DATA / "meta.json", {"houses": {}})
    all_items = set()
    currents = {}
    # on ne garde que les HV de config.json (les anciens disparaissent du tableau de bord)
    for old in [h for h in meta.get("houses", {}) if h not in CONFIG["auction_houses"]]:
        meta["houses"].pop(old, None)
        shutil.rmtree(DATA / old, ignore_errors=True)
        (STATE / f"{old}.json.gz").unlink(missing_ok=True)

    for house, ah_id in CONFIG["auction_houses"].items():
        print(f"HV {house} (#{ah_id})")
        state_file = STATE / f"{house}.json.gz"
        state = load(state_file, {})
        auctions, last_mod = fetch_house(api, ah_id)
        if not auctions:
            print("  aucune annonce reçue — HV ignoré ce tour-ci")
            continue
        if last_mod and last_mod == state.get("last_modified"):
            print("  photo Blizzard inchangée depuis le dernier passage — rien à faire")
            all_items.update(int(k) for k in load(DATA / house / "current.json", {}).get("items", {}))
            meta["houses"].setdefault(house, {})["label"] = CONFIG.get("labels", {}).get(house, house)
            continue

        summary, snap = summarize(auctions)
        prev_ts = state.get("ts")
        fresh = prev_ts and (now.timestamp() - prev_ts) < 3 * 3600
        sold = estimate_sales(state.get("snap", {}), snap, summary) if fresh else {}
        sold = {int(k): v for k, v in sold.items()}

        shards = update_history(house, summary, sold, now)
        current = build_current(summary, shards, now)
        save(DATA / house / "current.json", {
            "ts": int(now.timestamp()),
            "items": {str(k): v for k, v in current.items()},
        })
        save(state_file, {"ts": int(now.timestamp()), "last_modified": last_mod, "snap": snap})
        currents[house] = {str(k): v for k, v in current.items()}
        meta["houses"][house] = {
            "label": CONFIG.get("labels", {}).get(house, house),
            "ah_id": ah_id,
            "ts": int(now.timestamp()),
            "listings": len(auctions),
            "items": len(summary),
            "sold_last_hour": sum(sold.values()),
        }
        all_items.update(summary)
        print(f"  {len(auctions)} annonces, {len(summary)} objets, {sum(sold.values())} unités vendues estimées")

    prof = update_professions(meta, now)
    for recs in (prof or {}).get("professions", {}).values():
        for r in recs:
            all_items.add(r["i"])
            all_items.update(rid for rid, _ in r["r"])
    for r in load(ROOT / "recipes.json", []):  # noms des composants absents de l'HV (achetés au PNJ)
        all_items.add(r["makes"]["id"])
        all_items.update(g["id"] for g in r["reagents"])
    all_items.update(w["id"] for w in load(ROOT / "watchlist.json", []))
    items = update_items(api, sorted(all_items), meta, now)

    # alertes Discord / e-mail
    alert_house = CONFIG.get("alerts", {}).get("house")
    if alert_house in currents:
        import alerts
        sent_file = STATE / "alerts_sent.json"
        sent = load(sent_file, {})
        sent = {k: v for k, v in sent.items() if now.timestamp() - v[0] < 7 * 86400}
        alerts.process(currents[alert_house], items, load(ROOT / "alerts.json", []), CONFIG, sent,
                       int(now.timestamp()), CONFIG.get("labels", {}).get(alert_house, alert_house))
        save(sent_file, sent)
    meta["updated"] = int(now.timestamp())
    meta["ah_cut"] = CONFIG["ah_cut"]
    save(DATA / "meta.json", meta)
    for name in ("recipes.json", "watchlist.json", "alerts.json"):
        src = ROOT / name
        if src.exists():
            (DATA / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def update_professions(meta, now):
    """Recettes de tous les métiers (fichiers du jeu) : vérifie une fois par jour s'il y a un nouveau build."""
    path = DATA / "professions.json"
    current = load(path, None)
    if current and now.timestamp() - meta.get("recipes_check_ts", 0) < 24 * 3600:
        return current
    try:
        import recipes_builder
        version = recipes_builder.latest_build()
        meta["recipes_check_ts"] = int(now.timestamp())
        if current and current.get("build") == version:
            return current
        version, data = recipes_builder.build(version)
        n = sum(len(v) for v in data["professions"].values())
        print(f"  Recettes : {n} recettes sur {len(data['professions'])} métiers (build {version})")
        save(path, data)
        return data
    except Exception as e:  # le reste de l'outil continue de fonctionner
        print(f"  ! Recettes non mises à jour : {e}")
        meta["recipes_check_ts"] = int(now.timestamp()) - 18 * 3600  # nouvel essai dans 6 h
        return current


def discover():
    """Teste une plage de numéros d'HV et affiche ceux qui contiennent des annonces."""
    api = Api()
    rs, ns = CONFIG["ruleset_id"], CONFIG["namespace"]
    for path in ("/data/wow/ruleset/index", f"/data/wow/ruleset/{rs}", f"/data/wow/ruleset/{rs}/auctions/index"):
        status, js, _ = api.get(path, ns)
        print(f"{path} -> {status}")
        if js:
            print(json.dumps(js, ensure_ascii=False)[:1500])
    print("\nNuméros d'HV qui répondent :")
    for ah_id in range(100, 141):
        status, js, _ = api.get(f"/data/wow/ruleset/{rs}/auctions/{ah_id}", ns)
        if status == 200 and js:
            n = len(js.get("auctions") or [])
            name = js.get("name") or (js.get("auction_house") or {}).get("name", "")
            print(f"  HV {ah_id}: {n} annonces {name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    args = ap.parse_args()
    discover() if args.discover else run()
