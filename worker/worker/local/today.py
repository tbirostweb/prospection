"""« Quoi faire aujourd'hui » : relances dues, meilleurs prospects, tournée — calculé depuis la base, sans réseau ni IA.

Mêmes règles que l'application (miroir de `web/lib/local.ts`, vérifié par un test) : relance à J+3 puis J+7, « sans réponse » à J+14 (ensuite on ne recontacte plus),
créneaux d'appel par métier, tournée = les meilleurs prospects pas encore contactés, dans l'ordre du plus proche voisin.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import quote

from .. import db
from . import signals
from .zone import haversine_km

FOLLOWUP_DAYS = (3, 7)
NO_REPLY_DAYS = 14
TIME_BY_ACTIVITY: list[tuple[tuple[str, ...], str]] = [
    (("restaurants", "bars", "traiteurs"), "entre 15 h et 17 h 30 (entre les deux services), jamais pendant le rush"),
    (("boulangeries",), "entre 14 h et 16 h (après le rush de midi)"),
    (("coiffure", "beaute", "soins_personnels"), "mardi à jeudi, 10 h – 11 h 30 ou 14 h – 15 h (jamais le samedi)"),
    (("plombiers", "electriciens", "couvreurs", "menuisiers", "peintres", "macons", "paysagistes"), "tôt (7 h 30 – 8 h 30) ou en fin de journée (17 h 30 – 19 h) : sur chantier la journée"),
    (("garages",), "8 h 30 – 10 h ou 14 h – 16 h"),
    (("commerces", "boutiques", "fleuristes", "opticiens", "artisans_art"), "mardi à jeudi, 10 h – 11 h 30 (magasin calme)"),
    (("avocats", "architectes", "sante", "veterinaires", "services"), "9 h – 10 h ou 17 h – 18 h (entre les rendez-vous)"),
    (("immobilier",), "9 h 30 – 11 h"),
    (("hebergements",), "10 h – 12 h (après les départs)"),
    (("fitness", "cours", "auto_ecoles"), "10 h – 12 h ou 14 h – 16 h (avant les cours du soir)"),
]
DEFAULT_TIME = "mardi à jeudi, 9 h 30 – 11 h 30 ou 14 h – 16 h 30"
OPEN_STATUSES = "('DISCOVERED','ENRICHED','AUDITED','QUALIFIED','TO_CONTACT')"
GOOD = "('TRES_BON','A_CONTACTER')"
CLEAN = "do_not_contact=0 AND excluded_reason IS NULL AND is_chain=0"
# FICHE PRÊTE (miroir de web/lib/local.ts READY_SQL) : traitement terminé sans erreur, enrichissement fait, site tranché (trouvé, en panne, ou absent
# après une VRAIE recherche) et au moins un moyen de contact. Seules ces fiches sont proposées : moins de prospects, mais exploitables tels quels.
READY = ("p.pipeline_stage='DONE' AND p.deep_enriched_at IS NOT NULL AND (p.phone IS NOT NULL OR p.email IS NOT NULL OR p.contact_form=1) "
         "AND (p.website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE') OR (p.website_status='NOT_FOUND' AND p.website_absence_confidence >= 0.6) "
         "OR JSON_EXTRACT(p.provenance, '$.import_csv') IS NOT NULL)")   # fiche importée : prête dès qu'elle a un contact


def best_time(activity_key: str | None) -> str:
    return next((t for keys, t in TIME_BY_ACTIVITY if activity_key in keys), DEFAULT_TIME)


def followup_due(p: dict, now: datetime | None = None) -> str | None:
    """« Relance 1 (J+3) », « Relance 2 (J+7) », « Sans réponse depuis N jours » ou None."""
    if p.get("status") != "CONTACTED" or not p.get("contacted_at"):
        return None
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    days = (now - p["contacted_at"]).total_seconds() / 86400
    n = int(p.get("followups") or 0)
    if n < len(FOLLOWUP_DAYS) and days >= FOLLOWUP_DAYS[n]:
        return f"Relance {n + 1} (J+{FOLLOWUP_DAYS[n]})"
    if n >= len(FOLLOWUP_DAYS) and days >= NO_REPLY_DAYS:
        return f"Sans réponse depuis {int(days)} jours"
    return None


def followups(conn, now: datetime | None = None) -> list[dict]:
    rows = db.fetch_all(conn, f"""SELECT id, company_name, trade_name, city, phone, email, activity_key, status, contacted_at, followups
                                  FROM local_prospects WHERE status='CONTACTED' AND contacted_at IS NOT NULL AND do_not_contact=0
                                  AND (auto_state IS NULL OR auto_state <> 'active') ORDER BY contacted_at""")   # relances auto : gérées par l'envoi
    return [{**r, "due": d} for r in rows if (d := followup_due(r, now))]


def _signals(r: dict) -> list:
    raw = r.get("buy_signals")
    try:
        sigs = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return []
    return sigs if isinstance(sigs, list) else []


def trigger_first(rows: list[dict]) -> list[dict]:
    """Les lignes avec un déclencheur FORT d'abord (le plus fort en tête), puis les autres ; l'ordre d'origine est gardé à égalité."""
    def key(r: dict) -> float:
        top = signals.strong(_signals(r))
        return -float(top[0]["strength"]) if top else 0.0
    return sorted(rows, key=key)


def top_prospects(conn, n: int = 3, fresh_only: bool = False) -> list[dict]:
    """Les meilleurs pas encore contactés : ceux qui ont un déclencheur fort d'abord, puis le score, les plus récents d'abord à score égal
    (la nouveauté est un signal). `fresh_only` (résumé du matin) : seulement ceux qui n'y ont JAMAIS été présentés (digest_featured_at IS NULL)."""
    fresh = " AND p.digest_featured_at IS NULL" if fresh_only else ""
    rows = db.fetch_all(conn, f"""SELECT id, company_name, trade_name, city, phone, email, activity_key, prospect_score, buy_signals, category
                                  FROM local_prospects p WHERE category IN {GOOD} AND status IN {OPEN_STATUSES} AND {CLEAN} AND {READY}{fresh}
                                  ORDER BY prospect_score DESC, discovered_at DESC LIMIT %s""", (n + 50,))
    rows = trigger_first(rows)[:n]
    for r in rows:
        sigs, top = _signals(r), signals.strong(_signals(r))
        r["trigger"] = bool(top)
        r["signal"] = (top or sigs or [{}])[0].get("label")
    return rows


def mark_featured(conn, ids: list[int]) -> None:
    """Mémorise que ces prospects ont été présentés dans le résumé du matin : ils n'y seront plus proposés."""
    if ids:
        db.execute(conn, "UPDATE local_prospects SET digest_featured_at=UTC_TIMESTAMP() WHERE id IN (%s)" % ",".join(["%s"] * len(ids)), tuple(ids))
        conn.commit()


def tour(conn, stops: int = 6) -> dict | None:
    """Tournée depuis le centre de la campagne la plus récente : {'campaign','stops': [...], 'km', 'maps_url'} ou None."""
    camp = db.fetch_one(conn, "SELECT id, name, latitude, longitude FROM local_campaigns WHERE latitude IS NOT NULL AND enabled=1 ORDER BY id DESC LIMIT 1")
    if not camp:
        return None
    origin = (float(camp["latitude"]), float(camp["longitude"]))
    pool = db.fetch_all(conn, f"""SELECT p.id, p.company_name, p.trade_name, p.address, p.city, p.latitude, p.longitude, p.activity_key, p.prospect_score
                                  FROM local_prospects p JOIN local_prospect_campaigns c ON c.prospect_id=p.id AND c.campaign_id=%s
                                  WHERE p.category IN {GOOD} AND p.status IN {OPEN_STATUSES} AND p.do_not_contact=0 AND p.excluded_reason IS NULL AND p.is_chain=0 AND p.latitude IS NOT NULL AND {READY}
                                  ORDER BY p.status='TO_CONTACT' DESC, p.prospect_score DESC LIMIT %s""", (camp["id"], stops * 4))
    out, here, km = [], origin, 0.0
    while pool and len(out) < stops:
        i = min(range(len(pool)), key=lambda k: haversine_km(here[0], here[1], float(pool[k]["latitude"]), float(pool[k]["longitude"])))
        p = pool.pop(i)
        pos = (float(p["latitude"]), float(p["longitude"]))
        km += haversine_km(here[0], here[1], *pos)
        out.append(p)
        here = pos
    if not out:
        return None
    km += haversine_km(here[0], here[1], *origin)
    pts = "|".join(f"{float(p['latitude']):.6f},{float(p['longitude']):.6f}" for p in out)
    o = f"{origin[0]:.6f},{origin[1]:.6f}"
    url = f"https://www.google.com/maps/dir/?api=1&travelmode=driving&origin={o}&destination={o}&waypoints={quote(pts, safe=',')}"
    return {"campaign": camp["name"], "campaign_id": camp["id"], "stops": out, "km": round(km), "maps_url": url}
