"""Google Places (New) « Text Search » comme SOURCE de découverte d'une campagne : toutes les entreprises que Google connaît, dès le départ.

Pour chaque (mot-clé d'activité × commune de la zone) — communes officielles (geo.api.gouv.fr) dont le centre est dans le rayon, au moins
MIN_POPULATION habitants (la commune de la campagne toujours), les plus peuplées d'abord — UNE requête « plombier Troyes » limitée
(`locationRestriction`) à un carré de 2 × BOX_KM km autour du centre de la commune, puis ses pages suivantes (`nextPageToken`, 20 résultats par
page, 3 pages = 60 au plus). Chaque page = UNE requête réservée dans le plafond mensuel (`places.reserve`) AVANT l'appel.

Conditions de Google Maps Platform (impératif) :
  * on ne stocke de Google QUE le `place_id` (provenance `places` {checked_at, place_id, had_website} et `local_prospect_sources`) ; nom, adresse,
    position et site de la fiche servent en mémoire, le temps de RATTACHER le résultat à son entreprise SIRENE, puis sont oubliés ;
  * rattachement : fiche déjà en base (même place_id, ou nom + code postal + distance : `places.matches`), sinon l'API Recherche d'entreprises
    (nom + code postal, établissements actifs et diffusables, dans le rayon) et `places.matches` ; plusieurs entreprises possibles = AUCUNE ;
  * la fiche prospect est créée avec les DONNÉES SIRENE (`store.upsert_establishment`), par les mêmes filtres que la découverte SIRENE
    (`runner.independent` : chaînes, NAF exclus dont stations de lavage ; un seul établissement par entreprise ; « ne plus contacter ») ;
  * un résultat sans correspondance SIRENE sûre est IGNORÉ (compté : `unmatched`) ;
  * le `websiteUri` n'est qu'une URL candidate, gardée en mémoire (`places._session`) et VÉRIFIÉE sur le site lui-même par le résolveur
    (`sitefinder.resolve(candidate_urls=…)`) quand la fiche est traitée — en tête du passage. Une fiche déjà « sans site » est vérifiée aussitôt.
    Fiche non traitée dans ce processus : l'URL est redemandée par Place Details (`places.recover_url`) ; le verdict (`site_checked`) est gardé.
Mémoire des requêtes (`local_places_queries`, migration 032) : mot-clé, code INSEE, date, compteurs — aucun contenu Google — et les fiches
rattachées (`local_places_query_prospects`, nos identifiants) : la même requête n'est pas refaite avant RECHECK_DAYS jours ; une autre campagne
sur la même commune récupère ses fiches sans appel. Plafond par passage (PER_RUN pages, une requête commencée va jusqu'à sa dernière page),
plafond mensuel, arrêt net sur 401/403/429 (`places._blocked`, nouvel essai une heure plus tard). Sans clé, budget 0 ou PER_RUN=0 : inactif.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import time

from .. import db
from ..config import log
from . import http, places, sirene, store, textmatch, zone

PER_RUN = places._env_int("LOCAL_PLACES_DISCOVERY_PER_RUN", 20)         # pages Text Search au plus par passage (le cron passe toutes les 15 min)
MIN_POPULATION = places._env_int("LOCAL_PLACES_MIN_POPULATION", 500)     # communes plus petites : couvertes par le carré de leur voisine
RECHECK_DAYS = 90
MAX_PAGES = 3                     # 60 résultats au plus par requête (limite de Google)
PAGE_SIZE = 20
BOX_KM = 4.0                      # demi-côté du carré autour du centre de la commune
RETRY_AFTER_S = 3600              # clé refusée / quota Google atteint : pas de nouvel essai avant une heure
FIELD_MASK = places.FIELD_MASK + ",nextPageToken"
NO_SITE = ("NOT_FOUND", "UNCERTAIN")

# Mot(s)-clé(s) Google par activité du catalogue (`naf.CATALOG`) ; une activité ajoutée dans les réglages peut porter `places: [...]`, sinon son
# libellé. Liste vide : activité non cherchée chez Google. Un mot-clé de plus = autant de requêtes que de communes : peu de mots, les plus courants.
KEYWORDS: dict[str, list[str]] = {
    "restaurants": ["restaurant"], "bars": ["bar"], "traiteurs": ["traiteur"], "boulangeries": ["boulangerie"], "coiffure": ["coiffeur"],
    "beaute": ["institut de beauté"], "garages": ["garage automobile"], "plombiers": ["plombier"], "electriciens": ["électricien"],
    "couvreurs": ["couvreur"], "menuisiers": ["menuisier"], "peintres": ["peintre en bâtiment"], "macons": ["maçon"], "paysagistes": ["paysagiste"],
    "fleuristes": ["fleuriste"], "commerces": ["épicerie", "boucherie"], "boutiques": ["boutique"], "immobilier": ["agence immobilière"],
    "hebergements": ["hôtel", "chambre d'hôtes"], "services": ["expert-comptable", "entreprise de nettoyage"], "associations": [],
    "photographes": ["photographe"], "fitness": ["salle de sport"], "soins_personnels": ["tatoueur", "toiletteur"], "auto_ecoles": ["auto-école"],
    "demenageurs": ["déménageur"], "opticiens": ["opticien"], "architectes": ["architecte"], "avocats": ["avocat"], "veterinaires": ["vétérinaire"],
    "sante": ["kinésithérapeute", "ostéopathe"], "artisans_art": ["artisan d'art"], "evenementiel": ["organisation d'événements"],
    "cours": ["centre de formation"],
}


def active() -> bool:
    return places.enabled() and PER_RUN > 0


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Ce que la campagne demande à Google ─────────────────────────────────────────────────────────────────
def keywords(camp: dict, catalog: dict) -> list[str]:
    """Mots-clés Google des activités de la campagne (codes NAF saisis à la main : aucun, Google ne connaît pas la NAF)."""
    acts = json.loads(camp["activities"]) if isinstance(camp.get("activities"), (str, bytes)) else (camp.get("activities") or [])
    out: list[str] = []
    for key in acts:
        entry = catalog.get(str(key))
        if not entry:
            continue
        words = entry["places"] if isinstance(entry.get("places"), list) else KEYWORDS.get(str(key), [str(entry.get("label") or "").lower()])
        out += [w.strip().lower() for w in words if w and w.strip()]
    return list(dict.fromkeys(out))


def communes(conn, camp: dict) -> list[dict]:
    """Communes de la zone (rayon de la campagne ; département entier pour une campagne « Département 10 »), la commune de la campagne d'abord."""
    deps = {camp.get("department")} | {r["department"] for r in db.fetch_all(conn, """SELECT DISTINCT p.department FROM local_prospects p
        JOIN local_prospect_campaigns c ON c.prospect_id = p.id WHERE c.campaign_id = %s AND p.department IS NOT NULL""", (camp["id"],))}
    whole = str(camp.get("city") or "").startswith("Département ")
    found = zone.communes_in_zone(float(camp["latitude"]), float(camp["longitude"]), float(camp["radius_km"]), deps - {None, ""}, conn, whole_department=whole)
    home = camp.get("commune_code")
    return [c for c in found if c["code"] == home] + [c for c in found if c["code"] != home and c["population"] >= MIN_POPULATION]


def units(conn, camp: dict, catalog: dict) -> list[tuple[str, dict]]:
    """(mot-clé, commune) à interroger : communes les plus peuplées d'abord, tous les mots-clés pour chacune."""
    kws = keywords(camp, catalog)
    return [(kw, c) for c in communes(conn, camp) for kw in kws] if kws else []


def body(keyword: str, commune: dict, token: str | None = None) -> dict:
    lat, lon = float(commune["latitude"]), float(commune["longitude"])
    dlat, dlon = BOX_KM / 111.0, BOX_KM / (111.32 * max(0.2, math.cos(math.radians(lat))))
    b: dict = {"textQuery": f"{keyword} {commune['name']}".strip(), "languageCode": "fr", "regionCode": "FR", "pageSize": PAGE_SIZE,
               "locationRestriction": {"rectangle": {"low": {"latitude": round(lat - dlat, 6), "longitude": round(lon - dlon, 6)},
                                                     "high": {"latitude": round(lat + dlat, 6), "longitude": round(lon + dlon, 6)}}}}
    if token:
        b["pageToken"] = token
    return b


# ── Mémoire des requêtes (aucun contenu Google) ─────────────────────────────────────────────────────────
def _key(keyword: str) -> str:
    return textmatch.fold(keyword)[:80]


def recent_query(conn, keyword: str, commune_code: str) -> dict | None:
    return db.fetch_one(conn, """SELECT id FROM local_places_queries WHERE keyword=%s AND commune_code=%s
                                 AND queried_at > UTC_TIMESTAMP() - INTERVAL %s DAY""", (_key(keyword), commune_code, RECHECK_DAYS))


def _remember(conn, keyword: str, commune_code: str, counts: dict, pids: set[int]) -> None:
    db.execute(conn, """INSERT INTO local_places_queries (keyword, commune_code, queried_at, pages, results, matched, created, unmatched)
                        VALUES (%s,%s,UTC_TIMESTAMP(),%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE queried_at=UTC_TIMESTAMP(), pages=VALUES(pages), results=VALUES(results), matched=VALUES(matched),
                                                created=VALUES(created), unmatched=VALUES(unmatched)""",
               (_key(keyword), commune_code, counts["pages"], counts["results"], counts["matched"], counts["created"], counts["unmatched"]))
    qid = db.fetch_one(conn, "SELECT id FROM local_places_queries WHERE keyword=%s AND commune_code=%s", (_key(keyword), commune_code))["id"]
    db.execute(conn, "DELETE FROM local_places_query_prospects WHERE query_id=%s", (qid,))
    for pid in pids:
        db.execute(conn, "INSERT IGNORE INTO local_places_query_prospects (query_id, prospect_id) VALUES (%s,%s)", (qid, pid))


def _link_known(conn, query_id: int, campaign_id: int) -> int:
    """Requête déjà faite (moins de 90 jours, peut-être pour une autre campagne) : ses fiches rejoignent cette campagne, sans appel."""
    with conn.cursor() as cur:
        return cur.execute("""INSERT IGNORE INTO local_prospect_campaigns (prospect_id, campaign_id)
                              SELECT prospect_id, %s FROM local_places_query_prospects WHERE query_id=%s""", (campaign_id, query_id))


# ── Un appel ────────────────────────────────────────────────────────────────────────────────────────────
def _page(conn, b: dict) -> tuple[dict | None, str | None]:
    """(réponse, motif d'arrêt). Réservée dans le plafond mensuel AVANT l'appel, rendue si Google n'a rien servi."""
    if not places.enabled():
        return None, "Google Places coupé pour ce passage"
    if not places.reserve(conn):
        return None, f"budget mensuel Google Places atteint ({places.MONTHLY_BUDGET} requêtes)"
    data, err, status = places.post_search(b, FIELD_MASK)
    if data is None:
        places._refund(conn)
        if status in (401, 403, 429):
            places._blocked = True                       # clé refusée / quota : plus aucun appel pendant ce processus
        return None, err or "Google Places : échec"
    return data, None


# ── Rattachement à SIRENE ───────────────────────────────────────────────────────────────────────────────
_CP = re.compile(r"\b(\d{5})\b")


def postal_code(address: str | None) -> str | None:
    found = _CP.findall(address or "")
    return found[-1] if found else None


def _as_company(x) -> dict:
    g = (lambda k: x.get(k)) if isinstance(x, dict) else (lambda k: getattr(x, k, None))
    return {"company_name": g("company_name") or g("name"), "trade_name": g("trade_name"), "postal_code": g("postal_code"), "city": g("city"),
            "latitude": g("latitude"), "longitude": g("longitude")}


AMBIGUOUS = object()


def _one(matches: list, siren_of) -> object | None:
    """Une seule entreprise (SIREN) possible : la plus proche de ses établissements ; plusieurs : AMBIGUOUS (douteux = non)."""
    if not matches:
        return None
    if len({siren_of(m) for m in matches}) > 1:
        return AMBIGUOUS
    return matches[0]


def known_prospect(conn, place: dict, cp: str | None):
    """Fiche déjà en base pour ce résultat : même place_id, sinon même entreprise (nom + code postal + distance). None / AMBIGUOUS sinon."""
    pid = place.get("id")
    row = db.fetch_one(conn, """SELECT * FROM local_prospects WHERE id IN (SELECT prospect_id FROM local_prospect_sources WHERE source='places' AND source_ref=%s)
                                OR JSON_UNQUOTE(JSON_EXTRACT(provenance, '$.places.place_id')) = %s LIMIT 1""", (pid, pid))
    if row or not cp:
        return row
    rows = db.fetch_all(conn, "SELECT * FROM local_prospects WHERE postal_code=%s ORDER BY id", (cp,))
    return _one([r for r in rows if places.matches(_as_company(r), place)], lambda r: r.get("siren") or f"#{r['id']}")


def sirene_match(conn, place: dict, cp: str | None, camp: dict):
    """Établissement SIRENE (actif, diffusable, dans le rayon de la campagne) de ce résultat, ou None / AMBIGUOUS."""
    shown = place.get("displayName")
    shown = shown.get("text") if isinstance(shown, dict) else shown
    if not cp or not shown:
        return None
    origin, radius = (float(camp["latitude"]), float(camp["longitude"])), float(camp["radius_km"])
    ests = [e for u in sirene.search_name(str(shown), cp, conn) for e in sirene.parse_unit(u, origin, radius)]
    seen: set[str] = set()
    ok = [e for e in ests if not (e.siret in seen or seen.add(e.siret)) and places.matches(_as_company(e), place)]
    loc = place.get("location") or {}
    if loc.get("latitude") is not None and loc.get("longitude") is not None:
        ok.sort(key=lambda e: zone.haversine_km(float(loc["latitude"]), float(loc["longitude"]), e.latitude, e.longitude)
                if e.latitude is not None and e.longitude is not None else 999)
    return _one(ok, lambda e: e.siren)


# ── Un résultat ─────────────────────────────────────────────────────────────────────────────────────────
def _attach(conn, camp: dict, ctx, place: dict, counts: dict, fetch, url_ids: set[int]) -> int | None:
    """Rattache UN résultat Google à sa fiche (existante ou créée avec les données SIRENE). Renvoie l'id du prospect, ou None (ignoré)."""
    from . import runner
    if (place.get("businessStatus") or "") == "CLOSED_PERMANENTLY" or not place.get("id"):
        counts["closed"] += 1
        return None
    cp = postal_code(place.get("formattedAddress"))
    row = known_prospect(conn, place, cp)
    if row is AMBIGUOUS:
        counts["unmatched"] += 1
        return None
    before: dict = {}
    if row is None:
        est = sirene_match(conn, place, cp, camp)
        if est is None or est is AMBIGUOUS:
            counts["unmatched"] += 1                    # aucune entreprise SIRENE sûre : résultat ignoré, rien n'est gardé
            return None
        if not runner.independent(est):
            counts["banned"] += 1                       # chaîne, franchise, NAF exclu (station de lavage…) : jamais importée
            return None
        if store.other_establishment(conn, est.siren, est.siret):
            counts["same_company"] += 1
            return None
        est.source = "places"
        pid, created = store.upsert_establishment(conn, est, camp["id"], ctx.catalog)
        counts["created"] += int(created)
        row = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    else:
        prov = row.get("provenance")
        prov = json.loads(prov) if isinstance(prov, (str, bytes)) else (prov or {})
        before = prov["places"] if isinstance(prov.get("places"), dict) else {}
        db.execute(conn, "INSERT IGNORE INTO local_prospect_campaigns (prospect_id, campaign_id) VALUES (%s,%s)", (row["id"], camp["id"]))
    pid = row["id"]
    counts["matched"] += 1
    url = places.usable_website(place.get("websiteUri"), _as_company(row))
    store.add_source(conn, pid, "places", place["id"])
    flag = {"checked_at": _now_iso(), "place_id": place["id"], "had_website": bool(place.get("websiteUri"))}
    if before.get("site_checked_at") and before.get("place_id") == place["id"]:
        flag.update(site_checked_at=before["site_checked_at"], site_checked=before.get("site_checked"))   # site déjà jugé : pas de nouvelle vérification
    elif place.get("websiteUri") and not url:
        flag.update(places.site_check(places.website_rejection(place.get("websiteUri"), _as_company(row))))   # réseau social, annuaire : rien à vérifier
    store.set_provenance(conn, pid, "places", flag)
    if row.get("website_status") is None:
        if url:
            places._session[pid] = url                  # candidat EN MÉMOIRE seulement : vérifié quand la fiche est traitée (en tête du passage)
            url_ids.add(pid)
    elif url and row.get("website_status") in NO_SITE and not flag.get("site_checked_at"):     # site jamais jugé (même interrogée avant) : URL en main
        conn.commit()
        try:
            from . import siterecheck
            counts["sites_found"] += int(siterecheck.verify_places_site(conn, row, url, fetch))
        except Exception as exc:  # noqa: BLE001 — une vérification en échec n'arrête jamais la découverte
            conn.rollback()
            log.warning("[local] Google Places : vérification du site de #%s en échec : %s", pid, exc)
    return pid


def _query(conn, camp: dict, ctx, keyword: str, commune: dict, fetch, seen: dict, url_ids: set[int], stats: dict) -> tuple[int, str | None]:
    """UNE requête (mot-clé, commune), toutes ses pages. Renvoie (pages appelées, motif d'arrêt). Arrêtée en route : rien n'est mémorisé."""
    counts = {"pages": 0, "results": 0, "matched": 0, "created": 0, "unmatched": 0, "banned": 0, "same_company": 0, "closed": 0, "sites_found": 0}
    pids: set[int] = set()
    token = None
    stop = None
    while counts["pages"] < MAX_PAGES:
        if token and places._transport is None:
            time.sleep(2)                                # le jeton de page suivante n'est pas valable immédiatement
        data, stop = _page(conn, body(keyword, commune, token))
        if data is None:
            break
        counts["pages"] += 1
        for pl in data.get("places") or []:
            if not isinstance(pl, dict):
                continue
            counts["results"] += 1
            gid = pl.get("id")
            if gid and gid in seen:                      # déjà vu pendant ce passage (commune voisine) : pas de second rattachement
                if seen[gid]:
                    pids.add(seen[gid])
                continue
            pid = _attach(conn, camp, ctx, pl, counts, fetch, url_ids)
            if gid:
                seen[gid] = pid
            if pid:
                pids.add(pid)
        conn.commit()
        token = data.get("nextPageToken")
        if not token:
            break
    for k, v in counts.items():
        if k == "pages":
            continue                                     # compté par `run` (places_requests)
        stats[f"places_{k}"] = stats.get(f"places_{k}", 0) + v
    if stop is None:
        _remember(conn, keyword, commune["code"], counts, pids)
        stats["places_queries"] = stats.get("places_queries", 0) + 1
    conn.commit()
    log.info("[local] Google Places « %s » %s : %d page(s), %d résultat(s), %d rattaché(s) dont %d créé(s), %d sans correspondance SIRENE%s",
             keyword, commune["name"], counts["pages"], counts["results"], counts["matched"], counts["created"], counts["unmatched"],
             f" — arrêt : {stop}" if stop else "")
    return counts["pages"], stop


def run(conn, camp: dict, ctx, stats: dict, fetch=None, deadline: float | None = None, url_limit: int = 30) -> bool:
    """Découverte Google Places d'une campagne, bornée (PER_RUN pages, `deadline`, `url_limit` sites candidats en attente de vérification).
    Renvoie True s'il reste des requêtes à faire (la campagne n'est pas terminée), False sinon (fini, ou module inactif)."""
    if not active() or stats.get("places_done"):
        return False
    if float(stats.get("places_retry_at") or 0) > time.time():
        return True
    try:
        todo = units(conn, camp, ctx.catalog)
    except http.ApiError as exc:
        stats["places_waiting"] = f"communes de la zone indisponibles : {exc}"[:160]
        return True
    stats.pop("places_waiting", None)
    used, seen, url_ids, pending, left = 0, {}, set(), False, 0
    for i, (kw, com) in enumerate(todo):
        known = recent_query(conn, kw, com["code"])
        if known:
            _link_known(conn, known["id"], camp["id"])
            continue
        if used >= PER_RUN or len(url_ids) >= url_limit or (deadline is not None and time.monotonic() > deadline):
            pending = True
            left = sum(1 for k, c in todo[i:] if not recent_query(conn, k, c["code"]))
            break
        try:
            n, stop = _query(conn, camp, ctx, kw, com, fetch, seen, url_ids, stats)
        except http.ApiError as exc:                     # SIRENE momentanément indisponible : la requête sera refaite (non mémorisée)
            conn.rollback()
            stats["places_waiting"] = f"rattachement SIRENE indisponible : {exc}"[:160]
            return True
        used += n
        stats["places_requests"] = stats.get("places_requests", 0) + n
        if stop:
            stats["places_waiting"] = stop[:160]
            if places._blocked:
                stats["places_retry_at"] = int(time.time()) + RETRY_AFTER_S
            pending = True
            left = sum(1 for k, c in todo[i:] if not recent_query(conn, k, c["code"]))
            break
    stats["places_units"] = len(todo)
    stats["places_units_left"] = left
    if not pending:
        stats["places_done"] = True
        stats.pop("places_retry_at", None)
    conn.commit()
    return pending
