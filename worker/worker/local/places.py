"""Google Places (New) « Text Search » : DÉCOUVRIR l'URL du site d'un prospect « sans site », jamais une source de contenu.
La découverte d'ENTREPRISES par Google Places (source des campagnes, rattachée à SIRENE) est dans `placesdiscovery.py`, avec le même budget.

Beaucoup de petites entreprises n'ont pas de site trouvé par nos recherches alors que leur fiche Google Maps en donne un. Le `websiteUri` renvoyé
sert UNIQUEMENT de candidat : il est passé au résolveur (`sitefinder.resolve(candidate_urls=…)`), qui le VÉRIFIE (SIRET, adresse, mentions légales)
comme n'importe quel candidat ; e-mail et téléphone sont lus SUR LE SITE. Conditions de Google Maps Platform :
  * on ne stocke durablement AUCUN contenu Places (ni téléphone, ni site, ni adresse, ni note, ni nom, ni position) : seulement le `place_id`
    (identifiant autorisé), dans la provenance `places` {checked_at, place_id, had_website} — jamais l'URL ;
  * le téléphone Google n'est jamais utilisé (le champ n'est même pas demandé) ;
  * un site confirmé est enregistré comme un site trouvé par l'app, avec la preuve lue sur le site lui-même (aucun bonus « source structurée »).
URL perdue : une fiche interrogée dont la fiche Google avait un site (`had_website`) mais qui n'a pas été vérifiée dans le même passage (l'URL n'est
qu'en mémoire) est reprise par Place Details (`recover_url` : GET places/{place_id}, champ `websiteUri` seul), dans le même budget. Le verdict de
la vérification est posé dans la provenance (`site_checked_at`, `site_checked` : « confirmed », « probable », « unreachable », « rejected:<raison> »,
« no_url », « gone ») : la fiche n'est plus jamais redemandée à Google pour cela.
Une seule requête par prospect (puis pas avant 90 jours). Plafond MENSUEL persistant en base : table `local_places_usage` (une ligne par mois, migration 030),
et QUOTIDIEN (quota Google, jour du Pacifique) : `local_places_daily` (migration 033), réservés AVANT l'appel. Les campagnes (découverte, fiches
nouvelles) laissent une réserve du jour et du mois aux vérifications des fiches existantes (`PURPOSE = "enrich"`, posé par emailsearch.main) :
la découverte ne peut plus priver l'enrichissement de son quota. Sans clé ou budget 0 : module inactif, aucun appel. Pourquoi le dernier appel a été
refusé (budget, quota, clé) : `stop_reason`, affiché dans l'état du passage d'enrichissement.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import time
from urllib.parse import quote, urlparse

import httpx

from .. import db
from ..config import USER_AGENT, log
from . import sitefinder, store, textmatch

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{}"
DETAILS_FIELD_MASK = "websiteUri"
FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.websiteUri,places.businessStatus,places.location"
API_KEY = os.getenv("LOCAL_GOOGLE_PLACES_API_KEY", "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip() or default)
    except ValueError:
        return default


MONTHLY_BUDGET = _env_int("LOCAL_GOOGLE_PLACES_MONTHLY_BUDGET", 900)    # sous les 1 000 requêtes gratuites mensuelles de ce niveau d'API ; 0 = désactivé
DAILY_BUDGET = _env_int("LOCAL_GOOGLE_PLACES_DAILY_BUDGET", 300)        # quota quotidien réglé chez Google (jour du Pacifique) ; 0 = pas de plafond du jour
ENRICH_DAILY_RESERVE = _env_int("LOCAL_PLACES_ENRICH_DAILY_RESERVE", 60)        # requêtes du jour gardées aux fiches existantes (≤ 1/5 du plafond)
ENRICH_MONTHLY_RESERVE = _env_int("LOCAL_PLACES_ENRICH_MONTHLY_RESERVE", 300)   # requêtes du mois gardées aux fiches existantes (≤ 1/5 du plafond)
RECHECK_DAYS = 90                    # une requête par prospect, pas de nouvelle avant 90 jours
BIAS_RADIUS_M = 5000
MAX_DISTANCE_KM = 3.0                # position Google / position SIRENE : au-delà, c'est un autre établissement
NAME_FRACTION = 0.67                 # part des mots distinctifs du nom retrouvée dans le nom Google
GOOGLE_HOSTS = {"google.com", "google.fr", "goo.gl", "g.page", "business.site", "maps.app.goo.gl", "googleusercontent.com", "gstatic.com"}
_transport: httpx.BaseTransport | None = None          # tests
_blocked = False                                       # clé refusée / quota Google atteint : plus aucun appel pendant ce processus
_session: dict[int, str | None] = {}                   # prospect -> URL, en mémoire seulement (reprise après une erreur dans le même passage)
PURPOSE = "campaign"                                   # « enrich » : passage d'enrichissement des fiches existantes (accès aux réserves)
stop_reason: str | None = None                         # pourquoi le dernier appel n'a pas été fait (budget, quota, clé), pour l'état du passage


def enabled() -> bool:
    return bool(API_KEY) and MONTHLY_BUDGET > 0 and not _blocked


# ── Budget mensuel persistant ───────────────────────────────────────────────────────────────────────
def _month(conn) -> str:
    return db.fetch_one(conn, "SELECT DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m') AS m")["m"]


def month_used(conn) -> int:
    """Requêtes Places du mois en cours (UTC), lues dans local_places_usage (migration 030)."""
    r = db.fetch_one(conn, "SELECT requests FROM local_places_usage WHERE month=%s", (_month(conn),))
    return int(r["requests"]) if r else 0


def quota_day() -> str:
    """Jour du quota Google (minuit heure du Pacifique), « 2026-10-27 »."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001 — base des fuseaux absente : décalage d'hiver, au pire une heure d'écart
        tz = _dt.timezone(_dt.timedelta(hours=-8))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d")


def _cap(budget: int, reserve_n: int) -> int:
    """Plafond d'un passage de campagne : le budget moins la réserve des fiches existantes (au plus un cinquième du budget)."""
    return budget if PURPOSE == "enrich" else budget - min(max(reserve_n, 0), budget // 5)


def day_used(conn) -> int:
    r = db.fetch_one(conn, "SELECT requests FROM local_places_daily WHERE day=%s", (quota_day(),))
    return int(r["requests"]) if r else 0


def reserve(conn) -> bool:
    """Réserve UNE requête du jour et du mois, atomiquement (UPDATE conditionnels), AVANT l'appel ; commit aussitôt : les compteurs survivent à tout
    rollback. Refus : `stop_reason` dit pourquoi (plafond du jour, du mois, ou réserve gardée aux fiches existantes)."""
    global stop_reason
    m, day = _month(conn), quota_day()
    db.execute(conn, "INSERT IGNORE INTO local_places_usage (month, requests) VALUES (%s, 0)", (m,))
    if DAILY_BUDGET > 0:
        db.execute(conn, "INSERT IGNORE INTO local_places_daily (day, requests) VALUES (%s, 0)", (day,))
        with conn.cursor() as cur:
            n = cur.execute("UPDATE local_places_daily SET requests = requests + 1, budget = %s WHERE day=%s AND requests < %s",
                            (DAILY_BUDGET, day, _cap(DAILY_BUDGET, ENRICH_DAILY_RESERVE)))
        if not n:
            conn.commit()
            stop_reason = (f"quota Google du jour atteint ({DAILY_BUDGET} requêtes)" if PURPOSE == "enrich"
                           else f"réserve du jour gardée aux fiches existantes ({min(ENRICH_DAILY_RESERVE, DAILY_BUDGET // 5)} requêtes)")
            return False
    with conn.cursor() as cur:
        n = cur.execute("UPDATE local_places_usage SET requests = requests + 1, budget = %s WHERE month=%s AND requests < %s",
                       (MONTHLY_BUDGET, m, _cap(MONTHLY_BUDGET, ENRICH_MONTHLY_RESERVE)))     # le plafond est écrit pour la page Statistiques (migration 031)
    if not n and DAILY_BUDGET > 0:
        db.execute(conn, "UPDATE local_places_daily SET requests = GREATEST(requests - 1, 0) WHERE day=%s", (day,))
    conn.commit()
    if not n:
        stop_reason = f"budget mensuel Google Places atteint ({MONTHLY_BUDGET} requêtes)"
    return bool(n)


def _refund(conn) -> None:
    """Appel qui n'a rien consommé côté Google (réseau, clé refusée) : la requête réservée est rendue."""
    db.execute(conn, "UPDATE local_places_usage SET requests = GREATEST(requests - 1, 0) WHERE month=%s", (_month(conn),))
    if DAILY_BUDGET > 0:
        db.execute(conn, "UPDATE local_places_daily SET requests = GREATEST(requests - 1, 0) WHERE day=%s", (quota_day(),))
    conn.commit()


def block(status: int | None) -> None:
    """Clé refusée ou quota Google atteint (401 / 403 / 429) : plus aucun appel pendant ce processus."""
    global _blocked, stop_reason
    _blocked = True
    stop_reason = f"Google Places a refusé l'appel (HTTP {status} : quota atteint ou clé refusée)"


# ── Appel ───────────────────────────────────────────────────────────────────────────────────────────
def build_body(company: dict) -> dict:
    parts = [company.get("trade_name") or company.get("company_name") or "", company.get("address") or ""]
    addr = textmatch.fold(parts[1])
    if company.get("postal_code") and company["postal_code"] not in addr:
        parts.append(company["postal_code"])
    if company.get("city") and textmatch.fold(company["city"]) not in addr:
        parts.append(company["city"])
    body: dict = {"textQuery": " ".join(p for p in parts if p).strip(), "languageCode": "fr", "regionCode": "FR", "maxResultCount": 3}
    lat, lon = company.get("latitude"), company.get("longitude")
    if lat is not None and lon is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": float(lat), "longitude": float(lon)}, "radius": float(BIAS_RADIUS_M)}}
    return body


def post_search(body: dict, field_mask: str = FIELD_MASK, key: str | None = None) -> tuple[dict | None, str | None, int | None]:
    """UN appel Text Search : (réponse JSON, erreur, code HTTP). `None` = l'appel a échoué (réseau, clé refusée, quota) : ce n'est PAS « aucune fiche ».
    Ne réserve rien : l'appelant réserve (`reserve`) avant et rend (`_refund`) après un échec."""
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=15, transport=_transport) as c:
            r = c.post(PLACES_URL, json=body, headers={"X-Goog-Api-Key": key or API_KEY, "X-Goog-FieldMask": field_mask,
                                                       "Content-Type": "application/json", "User-Agent": USER_AGENT})
    except httpx.TimeoutException:
        return None, "Google Places : délai dépassé", None
    except httpx.HTTPError as exc:
        return None, f"Google Places injoignable : {exc.__class__.__name__}", None
    finally:
        log.debug("[local] Google Places : %d ms", int((time.monotonic() - t0) * 1000))
    if r.status_code in (401, 403, 429):
        return None, f"Google Places : clé refusée ou quota atteint (HTTP {r.status_code}) — vérifie LOCAL_GOOGLE_PLACES_API_KEY", r.status_code
    if r.status_code != 200:
        return None, f"Google Places HTTP {r.status_code}", r.status_code
    try:
        data = r.json()
        return (data if isinstance(data, dict) else {}), None, 200
    except ValueError:
        return None, "Google Places : réponse illisible", 200


def get_details(place_id: str, key: str | None = None) -> tuple[dict | None, str | None, int | None]:
    """UN appel Place Details (champ `websiteUri` seul) : (réponse JSON, erreur, code HTTP), comme `post_search`. Ne réserve rien."""
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=15, transport=_transport) as c:
            r = c.get(DETAILS_URL.format(quote(place_id, safe="")), params={"languageCode": "fr"},
                      headers={"X-Goog-Api-Key": key or API_KEY, "X-Goog-FieldMask": DETAILS_FIELD_MASK, "User-Agent": USER_AGENT})
    except httpx.TimeoutException:
        return None, "Google Places : délai dépassé", None
    except httpx.HTTPError as exc:
        return None, f"Google Places injoignable : {exc.__class__.__name__}", None
    finally:
        log.debug("[local] Google Places (Place Details) : %d ms", int((time.monotonic() - t0) * 1000))
    if r.status_code in (401, 403, 429):
        return None, f"Google Places : clé refusée ou quota atteint (HTTP {r.status_code}) — vérifie LOCAL_GOOGLE_PLACES_API_KEY", r.status_code
    if r.status_code != 200:
        return None, f"Google Places HTTP {r.status_code}", r.status_code
    try:
        data = r.json()
        return (data if isinstance(data, dict) else {}), None, 200
    except ValueError:
        return None, "Google Places : réponse illisible", 200


def search_text(company: dict, key: str | None = None) -> tuple[list[dict] | None, str | None]:
    """(résultats, erreur). `None` = l'appel a échoué (réseau, clé refusée, quota) : ce n'est PAS « aucune fiche »."""
    data, err, _status = post_search(build_body(company), key=key)
    if data is None:
        return None, err
    places = data.get("places") or []
    return ([p for p in places if isinstance(p, dict)] if isinstance(places, list) else []), None


# ── Rapprochement ───────────────────────────────────────────────────────────────────────────────────
def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def usable_website(uri: str | None, company: dict) -> str | None:
    """URL du site à proposer au résolveur (origine seule), ou None : réseau social, annuaire, page Google ou domaine d'annuaire ignorés."""
    if not uri or not isinstance(uri, str):
        return None
    p = urlparse(uri.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    domain = sitefinder.canonical_domain(uri)
    if sitefinder._in_set(domain, sitefinder.NOT_OFFICIAL | sitefinder.SOCIAL | GOOGLE_HOSTS) or sitefinder.looks_like_directory(domain, company):
        return None
    return sitefinder._origin(uri.strip())


def website_rejection(uri: str | None, company: dict) -> str:
    """Raison courte pour laquelle le site de la fiche Google n'est pas un candidat (`usable_website` = None). Un réseau social n'est pas enregistré
    comme profil de la fiche : ce serait conserver un contenu Google Places (seul le place_id peut l'être)."""
    if not uri or not isinstance(uri, str):
        return "no_url"
    p = urlparse(uri.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        return "rejected:invalid"
    domain = sitefinder.canonical_domain(uri)
    if sitefinder._in_set(domain, sitefinder.SOCIAL):
        return "rejected:social"
    if sitefinder._in_set(domain, GOOGLE_HOSTS):
        return "rejected:google"
    return "rejected:directory"


def matches(company: dict, place: dict) -> bool:
    """Ce résultat est-il BIEN cet établissement ? Nom distinctif, même code postal ou même ville, pas fermé, pas trop loin. Douteux = non."""
    if (place.get("businessStatus") or "") == "CLOSED_PERMANENTLY":
        return False
    shown = (place.get("displayName") or {}).get("text") if isinstance(place.get("displayName"), dict) else place.get("displayName")
    ok_name = False
    for name in {company.get("trade_name"), company.get("company_name")} - {None, ""}:
        needed = textmatch.distinctive_tokens(name) or textmatch.name_tokens(name)
        if needed and textmatch.token_fraction(needed, shown or "") >= NAME_FRACTION:
            ok_name = True
    if not ok_name:
        return False
    address = place.get("formattedAddress") or ""
    cp, city = company.get("postal_code"), textmatch.fold(company.get("city"))
    same_place = bool(cp and textmatch.digits(cp) and textmatch.digits(cp) in re_digits(address)) or bool(city and city in textmatch.fold(address))
    if not same_place:
        return False
    loc, lat, lon = place.get("location") or {}, company.get("latitude"), company.get("longitude")
    if lat is not None and lon is not None and loc.get("latitude") is not None and loc.get("longitude") is not None:
        try:
            if _km(float(lat), float(lon), float(loc["latitude"]), float(loc["longitude"])) > MAX_DISTANCE_KM:
                return False
        except (TypeError, ValueError):
            return False
    return True


def re_digits(text: str) -> str:
    """Chiffres du texte avec des espaces entre les groupes, pour chercher un code postal comme mot entier."""
    import re
    return " ".join(re.findall(r"\d+", text))


def pick(company: dict, places: list[dict]) -> tuple[str | None, str | None, bool]:
    """(place_id, URL candidate, la fiche avait un site ?) pour le premier résultat qui correspond ; (None, None, False) sinon."""
    for pl in places:
        if matches(company, pl):
            return pl.get("id"), usable_website(pl.get("websiteUri"), company), bool(pl.get("websiteUri"))
    return None, None, False


# ── Intégration ─────────────────────────────────────────────────────────────────────────────────────
def _checked_recently(row: dict) -> bool:
    prov = row.get("provenance")
    prov = json.loads(prov) if isinstance(prov, (str, bytes)) else (prov or {})
    at = (prov.get("places") or {}).get("checked_at") if isinstance(prov.get("places"), dict) else None
    if not at:
        return False
    try:
        when = _dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - when).days < RECHECK_DAYS


def _places_prov(row: dict) -> dict:
    prov = row.get("provenance")
    prov = json.loads(prov) if isinstance(prov, (str, bytes)) else (prov or {})
    return prov["places"] if isinstance(prov.get("places"), dict) else {}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def site_check(verdict: str) -> dict:
    """Champs de provenance du verdict sur le site de la fiche Google (aucun contenu Google : notre date et notre conclusion)."""
    return {"site_checked_at": _now_iso(), "site_checked": verdict[:60]}


def record_site_check(conn, pid: int, verdict: str) -> None:
    """Verdict de la vérification du site de la fiche Google, posé dans la provenance `places` (si elle existe) : plus de Place Details pour elle."""
    v = site_check(verdict)
    db.execute(conn, """UPDATE local_prospects SET provenance = JSON_SET(provenance, '$.places.site_checked_at', %s, '$.places.site_checked', %s)
                        WHERE id=%s AND JSON_TYPE(JSON_EXTRACT(provenance, '$.places')) = 'OBJECT'""", (v["site_checked_at"], v["site_checked"], pid))


def needs_recovery(row: dict) -> bool:
    """Fiche Google avec un site, place_id connu, site pas encore vérifié : l'URL (perdue) est à redemander par Place Details."""
    pl = _places_prov(row)
    return pl.get("had_website") is True and isinstance(pl.get("place_id"), str) and bool(pl["place_id"]) and not pl.get("site_checked_at")


def recover_url(conn, row: dict, company: dict) -> tuple[str | None, bool]:
    """URL du site de la fiche Google retrouvée par Place Details (`websiteUri` seul, UNE requête réservée dans le budget du mois), à VÉRIFIER.
    Renvoie (URL candidate ou None, conclu ?). Non conclu (False) : appel impossible (inactif, budget, réseau, clé refusée / quota : arrêt du module
    pour le processus) — rien n'est écrit, la fiche sera reprise. Conclu sans URL utilisable : le verdict (« no_url », « rejected:social »…, « gone »
    pour une fiche Google disparue) est posé aussitôt. L'URL n'est jamais stockée (mémoire du processus seulement)."""
    pid = row["id"]
    if pid in _session:
        return _session[pid], True
    if not needs_recovery(row):
        return None, True
    if not enabled():
        return None, False
    if not reserve(conn):
        log.info("[local] Google Places : %s — aucun appel", stop_reason)
        return None, False
    data, err, status = get_details(_places_prov(row)["place_id"])
    if data is None:
        _refund(conn)
        if status in (401, 403, 429):
            block(status)
        if status in (400, 404):                             # place_id périmé : la fiche Google n'existe plus sous cet identifiant
            record_site_check(conn, pid, "gone")
            conn.commit()
            return None, True
        log.warning("[local] %s", err)
        return None, False
    uri = data.get("websiteUri")
    url = usable_website(uri, company)
    if not url:
        record_site_check(conn, pid, website_rejection(uri, company))
    conn.commit()
    _session[pid] = url
    log.info("[local] Google Places #%s : site de la fiche redemandé (Place Details), %s (requêtes du mois : %d/%d)", pid,
             "proposé à la vérification" if url else "inutilisable", month_used(conn), MONTHLY_BUDGET)
    return url, True


def candidate_url(conn, row: dict, company: dict) -> str | None:
    """URL du site indiquée par Google Maps pour ce prospect, à VÉRIFIER (jamais à croire). None si inactif, déjà interrogé il y a moins de 90 jours,
    budget du mois atteint, appel en échec ou aucune fiche qui corresponde. Une seule requête par prospect ; seule la provenance `places` est écrite."""
    pid = row["id"]
    if _checked_recently(row):
        if pid in _session:
            return _session[pid]                             # déjà interrogé : l'URL n'est PAS en base, seulement en mémoire pour ce processus
        return recover_url(conn, row, company)[0]            # URL perdue (autre passage) et site jamais vérifié : Place Details
    if not enabled():
        return None
    if not reserve(conn):
        log.info("[local] Google Places : %s — aucun appel", stop_reason)
        return None
    places, err = search_text({**company, "latitude": row.get("latitude"), "longitude": row.get("longitude")})
    if places is None:
        _refund(conn)
        if "refusée" in (err or ""):
            block(None)                                      # inutile de brûler le reste du passage sur une clé refusée
        log.warning("[local] %s", err)
        return None
    located = {**company, "latitude": row.get("latitude"), "longitude": row.get("longitude")}
    place_id, url, had = pick(located, places)
    flag = {"checked_at": _now_iso(), "place_id": place_id, "had_website": had}
    if had and not url:                                      # réseau social, annuaire… : rien à vérifier, jamais redemandé
        uri = next((pl.get("websiteUri") for pl in places if matches(located, pl)), None)
        flag.update(site_check(website_rejection(uri, company)))
    store.set_provenance(conn, pid, "places", flag)
    conn.commit()
    _session[pid] = url
    log.info("[local] Google Places #%s : %d résultat(s), fiche %s, site %s (requêtes du mois : %d/%d)", pid, len(places),
             "retenue" if place_id else "non retenue", "proposé à la vérification" if url else "aucun", month_used(conn), MONTHLY_BUDGET)
    return url
