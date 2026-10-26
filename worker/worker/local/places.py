"""Google Places (New) « Text Search » : DÉCOUVRIR l'URL du site d'un prospect « sans site », jamais une source de contenu.

Beaucoup de petites entreprises n'ont pas de site trouvé par nos recherches alors que leur fiche Google Maps en donne un. Le `websiteUri` renvoyé
sert UNIQUEMENT de candidat : il est passé au résolveur (`sitefinder.resolve(candidate_urls=…)`), qui le VÉRIFIE (SIRET, adresse, mentions légales)
comme n'importe quel candidat ; e-mail et téléphone sont lus SUR LE SITE. Conditions de Google Maps Platform :
  * on ne stocke durablement AUCUN contenu Places (ni téléphone, ni site, ni adresse, ni note, ni nom, ni position) : seulement le `place_id`
    (identifiant autorisé), dans la provenance `places` {checked_at, place_id, had_website} — jamais l'URL ;
  * le téléphone Google n'est jamais utilisé (le champ n'est même pas demandé) ;
  * un site confirmé est enregistré comme un site trouvé par l'app, avec la preuve lue sur le site lui-même (aucun bonus « source structurée »).
Une seule requête par prospect (puis pas avant 90 jours). Plafond MENSUEL persistant en base : table `local_places_usage` (une ligne par mois, migration 030),
réservé AVANT l'appel. Sans clé ou budget 0 : module inactif, aucun appel.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import time
from urllib.parse import urlparse

import httpx

from .. import db
from ..config import USER_AGENT, log
from . import sitefinder, store, textmatch

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.websiteUri,places.businessStatus,places.location"
API_KEY = os.getenv("LOCAL_GOOGLE_PLACES_API_KEY", "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip() or default)
    except ValueError:
        return default


MONTHLY_BUDGET = _env_int("LOCAL_GOOGLE_PLACES_MONTHLY_BUDGET", 900)    # sous les 1 000 requêtes gratuites mensuelles de ce niveau d'API ; 0 = désactivé
RECHECK_DAYS = 90                    # une requête par prospect, pas de nouvelle avant 90 jours
BIAS_RADIUS_M = 5000
MAX_DISTANCE_KM = 3.0                # position Google / position SIRENE : au-delà, c'est un autre établissement
NAME_FRACTION = 0.67                 # part des mots distinctifs du nom retrouvée dans le nom Google
GOOGLE_HOSTS = {"google.com", "google.fr", "goo.gl", "g.page", "business.site", "maps.app.goo.gl", "googleusercontent.com", "gstatic.com"}
_transport: httpx.BaseTransport | None = None          # tests
_blocked = False                                       # clé refusée / quota Google atteint : plus aucun appel pendant ce processus
_session: dict[int, str | None] = {}                   # prospect -> URL, en mémoire seulement (reprise après une erreur dans le même passage)


def enabled() -> bool:
    return bool(API_KEY) and MONTHLY_BUDGET > 0 and not _blocked


# ── Budget mensuel persistant ───────────────────────────────────────────────────────────────────────
def _month(conn) -> str:
    return db.fetch_one(conn, "SELECT DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m') AS m")["m"]


def month_used(conn) -> int:
    """Requêtes Places du mois en cours (UTC), lues dans local_places_usage (migration 030)."""
    r = db.fetch_one(conn, "SELECT requests FROM local_places_usage WHERE month=%s", (_month(conn),))
    return int(r["requests"]) if r else 0


def reserve(conn) -> bool:
    """Réserve UNE requête du mois, atomiquement (un UPDATE conditionnel), AVANT l'appel ; commit aussitôt : le compteur survit à tout rollback."""
    m = _month(conn)
    db.execute(conn, "INSERT IGNORE INTO local_places_usage (month, requests) VALUES (%s, 0)", (m,))
    with conn.cursor() as cur:
        n = cur.execute("UPDATE local_places_usage SET requests = requests + 1, budget = %s WHERE month=%s AND requests < %s",
                       (MONTHLY_BUDGET, m, MONTHLY_BUDGET))     # le plafond est écrit pour la page Statistiques (migration 031)
    conn.commit()
    return bool(n)


def _refund(conn) -> None:
    """Appel qui n'a rien consommé côté Google (réseau, clé refusée) : la requête réservée est rendue."""
    db.execute(conn, "UPDATE local_places_usage SET requests = GREATEST(requests - 1, 0) WHERE month=%s", (_month(conn),))
    conn.commit()


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


def search_text(company: dict, key: str | None = None) -> tuple[list[dict] | None, str | None]:
    """(résultats, erreur). `None` = l'appel a échoué (réseau, clé refusée, quota) : ce n'est PAS « aucune fiche »."""
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=15, transport=_transport) as c:
            r = c.post(PLACES_URL, json=build_body(company), headers={"X-Goog-Api-Key": key or API_KEY, "X-Goog-FieldMask": FIELD_MASK,
                                                                       "Content-Type": "application/json", "User-Agent": USER_AGENT})
    except httpx.TimeoutException:
        return None, "Google Places : délai dépassé"
    except httpx.HTTPError as exc:
        return None, f"Google Places injoignable : {exc.__class__.__name__}"
    if r.status_code in (401, 403, 429):
        return None, f"Google Places : clé refusée ou quota atteint (HTTP {r.status_code}) — vérifie LOCAL_GOOGLE_PLACES_API_KEY"
    if r.status_code != 200:
        return None, f"Google Places HTTP {r.status_code}"
    try:
        places = (r.json() or {}).get("places") or []
        return [p for p in places if isinstance(p, dict)], None
    except (ValueError, TypeError, AttributeError):
        return None, "Google Places : réponse illisible"
    finally:
        log.debug("[local] Google Places : %d ms", int((time.monotonic() - t0) * 1000))


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


def candidate_url(conn, row: dict, company: dict) -> str | None:
    """URL du site indiquée par Google Maps pour ce prospect, à VÉRIFIER (jamais à croire). None si inactif, déjà interrogé il y a moins de 90 jours,
    budget du mois atteint, appel en échec ou aucune fiche qui corresponde. Une seule requête par prospect ; seule la provenance `places` est écrite."""
    global _blocked
    pid = row["id"]
    if _checked_recently(row):
        return _session.get(pid)                             # déjà interrogé : l'URL n'est PAS en base, seulement en mémoire pour ce processus
    if not enabled():
        return None
    if not reserve(conn):
        log.info("[local] Google Places : budget mensuel atteint (%d requêtes) — aucun appel", MONTHLY_BUDGET)
        return None
    places, err = search_text({**company, "latitude": row.get("latitude"), "longitude": row.get("longitude")})
    if places is None:
        _refund(conn)
        if "refusée" in (err or ""):
            _blocked = True                                  # inutile de brûler le reste du passage sur une clé refusée
        log.warning("[local] %s", err)
        return None
    place_id, url, had = pick({**company, "latitude": row.get("latitude"), "longitude": row.get("longitude")}, places)
    store.set_provenance(conn, pid, "places", {"checked_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "place_id": place_id, "had_website": had})
    conn.commit()
    _session[pid] = url
    log.info("[local] Google Places #%s : %d résultat(s), fiche %s, site %s (requêtes du mois : %d/%d)", pid, len(places),
             "retenue" if place_id else "non retenue", "proposé à la vérification" if url else "aucun", month_used(conn), MONTHLY_BUDGET)
    return url
