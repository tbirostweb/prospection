"""Santé et mesures de la prospection locale : moteurs, sources, funnel, campagnes, dégradations. LECTURE SEULE (aucune modification).

Sert : la section Santé (Stats), `python -m worker.audit_system`, l'alerte de dégradation (`python -m worker.local.monitor`).
"""
from __future__ import annotations

from .. import db

QUALIFIED = ("TRES_BON", "A_CONTACTER", "A_EXAMINER")


def engines_status(conn) -> list[dict]:
    """Fournisseurs UTILISABLES (un fournisseur déclaré sans clé n’est ni « ok » ni en panne : exclu)."""
    from . import engines as engines_mod         # import tardif : évite un cycle (engines n'importe pas health)
    rows = db.fetch_all(conn, """SELECT engine, enabled, health, requests, successes, failures, captchas, timeouts, empty_results, useful_results, avg_ms,
                                        consecutive_failures, last_success_at, last_failure_at, last_error, cooldown_until, window_requests, window_start,
                                        (cooldown_until IS NOT NULL AND cooldown_until > UTC_TIMESTAMP()) AS cooling
                                 FROM local_engine_health WHERE COALESCE(configured, 1) = 1
                                 ORDER BY COALESCE(health, 60) DESC, engine""")
    now = db.fetch_one(conn, "SELECT UTC_TIMESTAMP() AS n")["n"]
    for r in rows:
        r["error_rate"] = round(100 * (r["failures"] or 0) / r["requests"], 1) if r["requests"] else None
        fresh_window = r["window_start"] is not None and (now - r["window_start"]).total_seconds() < 24 * 3600
        r["over_budget"] = bool(fresh_window and int(r["window_requests"] or 0) >= engines_mod.DAILY_BUDGET)
        # « budget » : pas une panne — un moteur qu'on ménage volontairement, qui redevient dispo dans la fenêtre de 24 h sans aucune action.
        r["state"] = ("désactivé" if not r["enabled"] else "cooldown" if r["cooling"] else "budget" if r["over_budget"]
                      else "ok" if (r["consecutive_failures"] or 0) == 0 else "instable")
    return rows


def funnel(conn, campaign_id: int | None = None) -> list[dict]:
    """Entonnoir : où l'on perd des prospects. Chaque étape = nombre de prospects qui l'ont franchie (pas un pourcentage global trompeur)."""
    join = "JOIN local_prospect_campaigns c ON c.prospect_id = p.id AND c.campaign_id = %s" if campaign_id else ""
    params = (campaign_id,) if campaign_id else ()
    r = db.fetch_one(conn, f"""SELECT COUNT(*) AS discovered,
        SUM(p.excluded_reason IS NULL AND p.is_chain = 0 AND p.do_not_contact = 0) AS eligible,
        SUM(p.website_status IS NOT NULL) AS searched,
        SUM(p.website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE')) AS identified,
        SUM(p.website_status = 'CONFIRMED') AS confirmed,
        SUM(p.audited_at IS NOT NULL) AS audited,
        SUM(p.phone IS NOT NULL OR p.email IS NOT NULL OR p.contact_form = 1) AS contacts,
        SUM(p.score_stage = 'FINAL' AND p.category IN ('TRES_BON','A_CONTACTER','A_EXAMINER')) AS qualified,
        SUM(p.category IN ('TRES_BON','A_CONTACTER')) AS to_contact
        FROM local_prospects p {join}""", params) or {}
    steps = [("discovered", "Entreprises découvertes"), ("eligible", "Pertinentes (hors chaînes, exclus, opposition)"), ("searched", "Site recherché"),
             ("identified", "Site identifié (confirmé, probable ou inaccessible)"), ("confirmed", "Site confirmé (≥ 0,90)"), ("audited", "Sites audités"),
             ("contacts", "Contact professionnel trouvé"), ("qualified", "Qualifiés (Très bon, À contacter, À examiner)"), ("to_contact", "À contacter ou mieux")]
    parent = [None, 0, 1, 2, 3, 3, 3, 2, 7]          # les pourcentages se lisent par rapport à l'étape dont l'étape découle
    out: list[dict] = []
    for i, (key, label) in enumerate(steps):
        n = int(r.get(key) or 0)
        base = out[parent[i]]["count"] if parent[i] is not None else 0
        out.append({"key": key, "label": label, "count": n, "of_parent": round(100 * n / base, 1) if base else None,
                    "parent": steps[parent[i]][0] if parent[i] is not None else None})
    return out


def campaign_metrics(conn, cid: int) -> dict:
    r = db.fetch_one(conn, """SELECT COUNT(*) AS discovered,
        SUM(p.excluded_reason IS NULL AND p.is_chain = 0 AND p.do_not_contact = 0) AS eligible,
        SUM(p.website_status = 'CONFIRMED') AS confirmed, SUM(p.website_status = 'PROBABLE') AS probable, SUM(p.website_status = 'UNCERTAIN') AS uncertain,
        SUM(p.website_status = 'NOT_FOUND') AS not_found, SUM(p.website_status = 'UNREACHABLE') AS unreachable,
        SUM(p.phone IS NOT NULL OR p.email IS NOT NULL OR p.contact_form = 1) AS contacts, SUM(p.audited_at IS NOT NULL) AS audits,
        SUM(p.score_stage = 'FINAL' AND p.category IN ('TRES_BON','A_CONTACTER','A_EXAMINER')) AS qualified,
        SUM(p.category = 'TRES_BON') AS tres_bon, SUM(p.category = 'A_CONTACTER') AS a_contacter, SUM(p.category = 'A_EXAMINER') AS a_examiner,
        SUM(p.category = 'IGNORER') AS ignored, SUM(p.pipeline_stage = 'ERROR') AS errors,
        SUM(p.website_status IS NOT NULL) AS processed
        FROM local_prospects p JOIN local_prospect_campaigns c ON c.prospect_id = p.id WHERE c.campaign_id = %s""", (cid,)) or {}
    t = db.fetch_one(conn, "SELECT COALESCE(SUM(duration_ms),0) AS ms, COALESCE(SUM(processed),0) AS n FROM local_run_metrics WHERE campaign_id=%s", (cid,)) or {}
    out = {k: int(v or 0) for k, v in r.items()}
    out["total_time_s"] = round(int(t["ms"]) / 1000, 1)
    out["avg_time_per_prospect_s"] = round(int(t["ms"]) / 1000 / int(t["n"]), 1) if int(t["n"] or 0) else None
    return out


def error_stats(conn, days: int = 7) -> list[dict]:
    return db.fetch_all(conn, """SELECT category, COUNT(*) AS n, MAX(at) AS last_at FROM local_events WHERE at >= UTC_TIMESTAMP() - INTERVAL %s DAY
                                 GROUP BY category ORDER BY n DESC""", (days,))


def source_status(conn, days: int = 7) -> list[dict]:
    """Une ligne par source utilisée : dernière observation, erreurs récentes, prospects produits."""
    out = []
    for src, label in (("sirene", "API Recherche d'Entreprises (SIRENE)"), ("bodacc", "BODACC"), ("websearch", "Recherche du site (SearXNG)"),
                       ("osm", "OpenStreetMap (Overpass)")):
        n = db.fetch_one(conn, "SELECT COUNT(*) AS n, MAX(seen_at) AS last_at FROM local_prospect_sources WHERE source=%s", (src,))
        e = db.fetch_one(conn, "SELECT COUNT(*) AS n, MAX(at) AS last_at FROM local_events WHERE source=%s AND at >= UTC_TIMESTAMP() - INTERVAL %s DAY", (src if src != "websearch" else "search", days))
        out.append({"source": src, "label": label, "prospects": n["n"], "last_seen_at": n["last_at"], "errors_recent": e["n"], "last_error_at": e["last_at"]})
    return out


def run_rates(conn, hours_from: int, hours_to: int) -> dict:
    """Taux de sites trouvés / recherches vides / erreurs sur la fenêtre [now-hours_from, now-hours_to] (heures)."""
    r = db.fetch_one(conn, """SELECT COALESCE(SUM(processed),0) AS processed, COALESCE(SUM(confirmed+probable),0) AS found, COALESCE(SUM(not_found),0) AS not_found,
                                     COALESCE(SUM(searches),0) AS searches, COALESCE(SUM(searches_empty),0) AS empties, COALESCE(SUM(errors),0) AS errors
                              FROM local_run_metrics WHERE started_at >= UTC_TIMESTAMP() - INTERVAL %s HOUR AND started_at < UTC_TIMESTAMP() - INTERVAL %s HOUR""",
                       (hours_from, hours_to)) or {}
    p, s = int(r["processed"] or 0), int(r["searches"] or 0)
    return {"processed": p, "found_rate": round(int(r["found"]) / p, 3) if p else None, "empty_search_rate": round(int(r["empties"]) / s, 3) if s else None,
            "error_rate": round(int(r["errors"]) / p, 3) if p else None, "searches": s}


def degradations(conn) -> list[dict]:
    """Anomalies détectées automatiquement : [{"level": "critical|warning", "code", "message"}]. Ne produit RIEN sans base de comparaison suffisante."""
    out: list[dict] = []
    engines = engines_status(conn)
    down = [e for e in engines if e["state"] in ("cooldown", "désactivé", "budget")]
    if engines and len(down) == len(engines):
        if all(e["state"] == "budget" for e in engines):
            # Pas une panne : chaque moteur a un budget de requêtes par 24 h (on le ménage). Se résorbe seul, jamais critique, jamais répété tant que ça dure.
            out.append({"level": "warning", "code": "ALL_ENGINES_BUDGET",
                        "message": "tous les moteurs de recherche ont atteint leur budget de requêtes du jour : la recherche de site reprendra automatiquement, ce n'est pas une panne"})
        else:
            out.append({"level": "critical", "code": "ALL_ENGINES_DOWN", "message": "tous les moteurs de recherche sont en cooldown : la résolution des sites est à l'arrêt"})
    today, base = run_rates(conn, 24, 0), run_rates(conn, 24 * 8, 24)
    if today["processed"] >= 10 and base["processed"] >= 30 and base["found_rate"]:
        if today["found_rate"] < base["found_rate"] / 3 and base["found_rate"] >= 0.1:
            out.append({"level": "critical", "code": "SITE_RATE_DROP", "message": f"sites trouvés : {today['found_rate']:.0%} sur 24 h contre {base['found_rate']:.0%} les 7 jours précédents"})
    if today["searches"] >= 20 and (today["empty_search_rate"] or 0) >= 0.9 and (base["empty_search_rate"] is None or base["empty_search_rate"] < 0.7):
        out.append({"level": "critical", "code": "SEARCH_EMPTY", "message": f"SearXNG ne renvoie presque plus de résultats ({today['empty_search_rate']:.0%} de recherches vides sur 24 h)"})
    if today["processed"] >= 10 and (today["error_rate"] or 0) >= 0.3:
        out.append({"level": "warning", "code": "ERROR_RATE", "message": f"{today['error_rate']:.0%} des prospects traités sont en erreur sur 24 h"})
    src = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_events WHERE category='SOURCE_UNAVAILABLE' AND at >= UTC_TIMESTAMP() - INTERVAL 24 HOUR")["n"]
    if src >= 5:
        out.append({"level": "warning", "code": "SOURCE_UNAVAILABLE", "message": f"{src} indisponibilités de sources externes (SIRENE, BODACC, OSM, PageSpeed) sur 24 h"})
    stuck = db.fetch_one(conn, """SELECT COUNT(*) AS n FROM local_campaigns WHERE status='running' AND (last_run_at IS NULL OR last_run_at < UTC_TIMESTAMP() - INTERVAL 6 HOUR)
                                  AND enabled=1""")["n"]
    if stuck:
        out.append({"level": "warning", "code": "CAMPAIGN_STUCK", "message": f"{stuck} campagne(s) « en cours » sans activité depuis plus de 6 h"})
    # SIRENE : une découverte qui rend nettement moins que la précédente sur la même zone
    dis = db.fetch_all(conn, """SELECT id, name, coverage, last_run_at FROM local_campaigns WHERE coverage IS NOT NULL ORDER BY id DESC LIMIT 20""")
    import json
    for c in dis:
        cov = json.loads(c["coverage"]) if isinstance(c["coverage"], (str, bytes)) else c["coverage"]
        if cov and cov.get("partial") is False and cov.get("imported") == 0:
            out.append({"level": "warning", "code": "DISCOVERY_EMPTY", "message": f"campagne « {c['name']} » : la découverte n'a rien importé (API entreprises vide ou zone sans activité)"})
    return out
