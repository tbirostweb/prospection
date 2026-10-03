"""BODACC : événements de la vie de l'entreprise — créations, REPRISES (achat d'un fonds), déménagements, changements de nom, nouveaux
établissements, fermetures. Ce sont des MOMENTS où un site se commande (nouveau patron, nouvelle adresse, nouveau nom), jamais une preuve de besoin.
 On interroge le jeu ouvert
« annonces-commerciales » (OpenDataSoft) DIRECTEMENT par SIREN (lots de 20) : mesuré en réel, l'Aube compte ~2 000 créations par an, une
pagination départementale n'en voyait même pas la moitié. Seuls les SIREN de NOS prospects sont interrogés.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import http

API = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"
FIELDS = "dateparution,familleavis_lib,commercant,ville,cp,registre,numerodepartement,acte,modificationsgenerales,listeetablissements"
CHANGE_FAMILIES = ("Modifications diverses", "Ventes et cessions")
CLOSURE_FAMILIES = {"Radiations": "radiation", "Procédures collectives": "collective"}     # fermeture / difficulté : jamais prospecté
EVENT_KINDS = ("takeover", "move", "rename", "new_establishment", "change")                  # le détail est dans `events` ; `kind` garde « change »
RANK = {"radiation": 3, "collective": 3, "closure": 3, "creation": 2, "change": 1}
PAGE = 100


def _siren(registre) -> str | None:
    for r in registre or []:
        d = "".join(c for c in str(r) if c.isdigit())
        if len(d) == 9:
            return d
    return None


def _acte(rec: dict, key: str = "acte") -> dict:
    a = rec.get(key)
    if isinstance(a, str):
        import json
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


# Relevé en réel (Aube, 09/2026) : « transfert du siège social », « transfert de l'établissement principal », « Modification survenue sur
# l'administration, cessation d'activité, dissolution de la société », « siège et établissement principal acquis par achat ».
_CLOSED = re.compile(r"cessation d.activit|dissolution|liquidation|mise en sommeil|fermeture", re.I)
_MOVE = re.compile(r"transfert d[eu] (?:si[eè]ge|l.[ée]tablissement|fonds)|changement d.adresse|nouvelle adresse", re.I)
_RENAME = re.compile(r"d[ée]nomination|nom commercial|enseigne", re.I)
_NEW_ESTAB = re.compile(r"(?:cr[ée]ation|ouverture|adjonction) d.un [ée]tablissement|[ée]tablissement secondaire", re.I)
_BOUGHT = re.compile(r"achat|acquis|cession|reprise|location.g[ée]rance|apport", re.I)


def classify(rec: dict) -> str:
    """Nature de l'annonce : creation | takeover | move | rename | new_establishment | closure | change | radiation | collective."""
    fam = rec.get("familleavis_lib") or ""
    if fam in CLOSURE_FAMILIES:
        return CLOSURE_FAMILIES[fam]
    acte, mod = _acte(rec), _acte(rec, "modificationsgenerales")
    etab = _acte(rec, "listeetablissements").get("etablissement") or {}
    etab = etab[0] if isinstance(etab, list) and etab else etab if isinstance(etab, dict) else {}
    text = " ".join(str(x) for x in (acte.get("descriptif"), (acte.get("vente") or {}).get("categorieVente"), (acte.get("creation") or {}).get("categorieCreation"),
                                     mod.get("descriptif"), etab.get("origineFonds")) if x)
    if fam == "Ventes et cessions":
        return "takeover"                                        # le 1er SIREN de l'annonce est l'ACHETEUR (le vendeur n'est jamais pris)
    if fam == "Créations":
        return "takeover" if _BOUGHT.search(text) else "creation"
    if _CLOSED.search(text):
        return "closure"
    if _RENAME.search(text):
        return "rename"
    if _MOVE.search(text):
        return "move"
    if _NEW_ESTAB.search(text):
        return "new_establishment"
    return "change"


def fetch_creations(department: str, since: date | None = None, max_pages: int = 8, conn=None) -> dict[str, dict]:
    """{siren: {'kind': 'creation', 'published': 'AAAA-MM-JJ', 'registered': …, 'started': …, 'category': …}} des créations récentes du département."""
    since = since or date.today() - timedelta(days=365)
    where = f'familleavis_lib="Créations" and numerodepartement="{department}" and dateparution>=date\'{since.isoformat()}\''
    out: dict[str, dict] = {}
    for page in range(max_pages):
        data = http.get_json(API, {"where": where, "select": FIELDS, "limit": PAGE, "offset": page * PAGE,
                                   "order_by": "dateparution desc"}, conn=conn, cache_hours=24)
        recs = (data or {}).get("results") or []
        for rec in recs:
            siren = _siren(rec.get("registre"))
            if not siren or siren in out:
                continue
            a = _acte(rec)
            out[siren] = {"kind": "creation", "published": rec.get("dateparution"), "registered": a.get("dateImmatriculation"),
                          "started": a.get("dateCommencementActivite"), "category": (a.get("creation") or {}).get("categorieCreation")}
        if len(recs) < PAGE:
            break
    return out


def fetch_changes(department: str, since: date | None = None, max_pages: int = 3, conn=None) -> dict[str, dict]:
    """Changements notables (vente / cession, modification diverse) : signal secondaire, jamais un besoin."""
    since = since or date.today() - timedelta(days=180)
    fam = " or ".join(f'familleavis_lib="{f}"' for f in CHANGE_FAMILIES)
    where = f"({fam}) and numerodepartement=\"{department}\" and dateparution>=date'{since.isoformat()}'"
    out: dict[str, dict] = {}
    for page in range(max_pages):
        data = http.get_json(API, {"where": where, "select": FIELDS, "limit": PAGE, "offset": page * PAGE,
                                   "order_by": "dateparution desc"}, conn=conn, cache_hours=24)
        recs = (data or {}).get("results") or []
        for rec in recs:
            siren = _siren(rec.get("registre"))
            if siren and siren not in out:
                out[siren] = {"kind": "change", "published": rec.get("dateparution"), "family": rec.get("familleavis_lib")}
        if len(recs) < PAGE:
            break
    return out


def signal_for(siren: str, creations: dict, changes: dict) -> dict | None:
    """Signal BODACC d'un SIREN (création prioritaire), ou None."""
    return creations.get(siren) or changes.get(siren)


def lookup(sirens: list[str], since: date | None = None, conn=None, batch: int = 20) -> dict[str, dict]:
    """{siren: signal} pour ces SIREN — création (prioritaire, la plus récente) sinon changement notable (vente / cession / modification)."""
    since = since or date.today() - timedelta(days=548)
    fams = " or ".join(f'familleavis_lib="{f}"' for f in ("Créations",) + CHANGE_FAMILIES + tuple(CLOSURE_FAMILIES))
    out: dict[str, dict] = {}
    todo = [s for s in dict.fromkeys(sirens) if s and len(s) == 9 and s.isdigit()]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        registres = " or ".join('registre="%s"' % s for s in chunk)
        where = f"({registres}) and ({fams}) and dateparution>=date'{since.isoformat()}'"
        offset = 0
        while True:
            data = http.get_json(API, {"where": where, "select": FIELDS, "limit": PAGE, "offset": offset, "order_by": "dateparution desc"},
                                 conn=conn, cache_hours=24)
            recs = (data or {}).get("results") or []
            for rec in recs:
                siren = _siren(rec.get("registre"))
                if not siren or siren not in chunk:
                    continue
                kind = classify(rec)
                if kind == "creation":
                    a = _acte(rec)
                    sig = {"kind": "creation", "published": rec.get("dateparution"), "registered": a.get("dateImmatriculation"),
                           "started": a.get("dateCommencementActivite"), "category": (a.get("creation") or {}).get("categorieCreation")}
                else:
                    sig = {"kind": "change" if kind in EVENT_KINDS else kind, "published": rec.get("dateparution"), "family": rec.get("familleavis_lib")}
                prev = out.get(siren)
                if prev is None or (RANK[sig["kind"]] > RANK[prev["kind"]]) or (RANK[sig["kind"]] == RANK[prev["kind"]] and (sig.get("published") or "") > (prev.get("published") or "")):
                    events = (prev or {}).get("events") or []
                    out[siren] = {**sig, "events": events} if events else sig
                    prev = out[siren]
                if kind not in ("radiation", "collective"):
                    ev = {"kind": kind, "published": rec.get("dateparution")}
                    prev["events"] = sorted([*(prev.get("events") or []), ev], key=lambda e: e.get("published") or "", reverse=True)[:6]
            if len(recs) < PAGE:
                break
            offset += PAGE
    return out
