"""
Recettes de tous les métiers de WoW Forever, extraites des fichiers de données du jeu.

Les tables DB2 du client (SkillLineAbility, SpellEffect, SpellReagents, ItemSparse)
sont téléchargées en CSV depuis wago.tools pour le dernier build « wowf » (WoW Forever).
On en tire, pour chaque recette qui fabrique un objet : le métier, la compétence requise,
l'objet produit (et sa quantité moyenne) et les composants. Les prix PNJ (ItemSparse.BuyPrice)
servent pour les composants absents de l'HV (fils, fioles, flux, épices...).

Le résultat (professions.json) n'est recalculé que lorsqu'un nouveau build sort, ou une fois par semaine.
"""
import csv
import io
import time

import requests

WAGO = "https://wago.tools"
PRODUCT = "wowf"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/json;q=0.9,*/*;q=0.8",
}
CREATE_ITEM = 24  # type d'effet de sort « créer un objet »

# Composants vendus par les marchands : on prend toujours le prix PNJ, même si des revendeurs
# en mettent à l'HV plus cher (fils, fioles, flux, teintures, épices, eau, charbon...).
VENDOR_ITEMS = {
    2320, 2321, 4291, 8343, 14341,          # fils
    3371, 3372, 8925, 18256,                # fioles
    2880, 3466,                             # flux
    2324, 2325, 2604, 2605, 4340, 4341, 4342, 6260, 6261, 10290,  # teintures et décolorant
    2678, 2692, 3713, 159, 1179, 4289, 2596, 3857, 4470, 6217,    # épices, eau, lait, sel, charbon, bois, baguette
}

PROFESSIONS = {  # identifiant de ligne de compétence -> nom affiché
    171: "Alchimie",
    164: "Forge",
    333: "Enchantement",
    202: "Ingénierie",
    165: "Travail du cuir",
    197: "Couture",
    185: "Cuisine",
    129: "Secourisme",
    186: "Minage",
}


def _get(url, timeout=120, tries=4):
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"statut {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url} : {last}")


def latest_build():
    builds = _get(f"{WAGO}/api/builds", timeout=30).json()
    versions = [b.get("version", "") for b in builds.get(PRODUCT, []) if b.get("version")]
    if not versions:
        raise RuntimeError(f"aucun build « {PRODUCT} » publié sur wago.tools")

    def key(v):
        try:
            return tuple(int(p) for p in v.split("."))
        except ValueError:
            return (-1,)
    return max(versions, key=key)


def table(name, build):
    text = _get(f"{WAGO}/db2/{name}/csv?build={build}").content.decode("utf-8-sig")
    return csv.DictReader(io.StringIO(text))


def _int(row, *cols):
    for c in cols:
        v = row.get(c)
        if v not in (None, ""):
            try:
                return int(float(v))
            except ValueError:
                pass
    return 0


def build(build_version=None):
    """-> (version, {"professions": {...}, "vendor": {item_id: prix_unitaire}})"""
    version = build_version or latest_build()

    # 1) recettes de métiers : sort -> (métier, compétence)
    spells = {}
    for row in table("SkillLineAbility", version):
        line = _int(row, "SkillLine")
        if line not in PROFESSIONS:
            continue
        spell = _int(row, "Spell")
        if spell:
            skill = _int(row, "MinSkillLineRank") or _int(row, "TrivialSkillLineRankLow")
            prev = spells.get(spell)
            if prev is None or skill < prev[1]:
                spells[spell] = (line, skill)

    # 2) objet produit et quantité
    makes = {}
    for row in table("SpellEffect", version):
        spell = _int(row, "SpellID")
        if spell not in spells or _int(row, "Effect") != CREATE_ITEM:
            continue
        item = _int(row, "EffectItemType")
        if not item:
            continue
        base, sides = _int(row, "EffectBasePoints"), _int(row, "EffectDieSides")
        qty = base + (sides + 1) / 2 if sides > 1 else base + max(sides, 1)
        makes.setdefault(spell, (item, round(max(qty, 1), 2)))

    # 3) composants
    reagents = {}
    for row in table("SpellReagents", version):
        spell = _int(row, "SpellID")
        if spell not in makes:
            continue
        parts = []
        for i in range(8):
            rid, cnt = _int(row, f"Reagent_{i}"), _int(row, f"ReagentCount_{i}")
            if rid > 0 and cnt > 0:
                parts.append([rid, cnt])
        if parts:
            reagents[spell] = parts

    # 4) prix PNJ des composants (prix d'achat au marchand, par unité)
    needed = {r for parts in reagents.values() for r, _ in parts}
    vendor = {}
    for row in table("ItemSparse", version):
        iid = _int(row, "ID")
        if iid in needed:
            price = _int(row, "BuyPrice")
            stack = max(_int(row, "VendorStackCount"), 1)
            if price > 0:
                vendor[str(iid)] = round(price / stack)

    out = {}
    for spell, (item, qty) in makes.items():
        if spell not in reagents:
            continue
        line, skill = spells[spell]
        out.setdefault(PROFESSIONS[line], []).append({
            "s": spell, "i": item, "q": qty, "k": skill, "r": reagents[spell],
        })
    for recs in out.values():
        recs.sort(key=lambda r: (r["k"], r["i"]))
    fixed = sorted(int(k) for k in vendor if int(k) in VENDOR_ITEMS)
    return version, {"build": version, "professions": out, "vendor": vendor, "fixed": fixed}
