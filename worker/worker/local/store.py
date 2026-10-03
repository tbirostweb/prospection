"""Persistance de la prospection locale : dédoublonnage par SIRET (sinon empreinte prudente), fusion des sources, liste « ne plus contacter »."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .. import db
from . import naf as naf_mod
from .textmatch import fold, normalize_address

AUTO_STATUSES = ("DISCOVERED", "ENRICHED", "AUDITED", "QUALIFIED")       # seuls statuts que le worker a le droit de modifier


def fingerprint(name: str | None, address: str | None, city: str | None) -> str:
    return hashlib.md5(f"{fold(name)}|{normalize_address(address)}|{fold(city)}".encode()).hexdigest()


def load_settings(conn) -> dict:
    rows = db.fetch_all(conn, """SELECT skey, value_json FROM settings WHERE skey IN
                                 ('local_home', 'local_weights', 'local_thresholds', 'local_activities', 'local_options', 'local_banned_brands', 'local_learning')""")
    out = {}
    for r in rows:
        v = r["value_json"]
        out[r["skey"]] = json.loads(v) if isinstance(v, (str, bytes)) else v
    return out


def is_do_not_contact(conn, siret: str | None = None, siren: str | None = None, domain: str | None = None, email: str | None = None) -> bool:
    row = db.fetch_one(conn, """SELECT 1 AS x FROM local_do_not_contact WHERE (siret IS NOT NULL AND siret = %s)
                                OR (siren IS NOT NULL AND siren = %s) OR (domain IS NOT NULL AND domain = %s)
                                OR (email IS NOT NULL AND email = %s) LIMIT 1""", (siret, siren, domain, email))
    return row is not None


def upsert_establishment(conn, est, campaign_id: int, catalog: dict | None = None, chain_fn=None) -> tuple[int, bool]:
    """Crée ou complète le prospect d'un établissement SIRENE. Renvoie (id, créé ?). Une entreprise déjà connue (même SIRET, ou même nom +
    adresse + ville sans SIRET) n'est JAMAIS dupliquée : ses sources et ses campagnes sont fusionnées."""
    key, label, _web = naf_mod.activity_of(est.naf_code, catalog)
    reasons = naf_mod.chain_signals(est.name, est.trade_name, est.company_size, est.employee_range, est.establishments_open, est.legal_category,
                                    est.naf_code)
    excluded = naf_mod.excluded_reason(est.naf_code, f"{est.trade_name or ''} {est.name}", legal_category=est.legal_category)
    fp = fingerprint(est.trade_name or est.name, est.address, est.city)
    geo_conf = 0.9 if est.latitude is not None and est.longitude is not None else (0.4 if est.postal_code else 0.1)
    kind = "NATIONAL" if reasons else None
    row = db.fetch_one(conn, "SELECT id, distance_km FROM local_prospects WHERE siret=%s OR fingerprint=%s LIMIT 1", (est.siret, fp))
    dnc = is_do_not_contact(conn, est.siret, est.siren)
    if row:
        pid, created = row["id"], False
        if est.distance_km is not None and (row["distance_km"] is None or est.distance_km < float(row["distance_km"])):
            db.execute(conn, "UPDATE local_prospects SET distance_km=%s WHERE id=%s", (est.distance_km, pid))
        if getattr(est, "revenue", None) is not None or getattr(est, "manager_name", None):
            db.execute(conn, """UPDATE local_prospects SET revenue=COALESCE(%s, revenue), revenue_year=COALESCE(%s, revenue_year),
                                manager_name=COALESCE(%s, manager_name) WHERE id=%s""", (est.revenue, est.revenue_year, est.manager_name, pid))
    else:
        pid = db.execute(conn, """INSERT INTO local_prospects (siren, siret, fingerprint, company_name, trade_name, activity_key, activity_label,
            naf_code, address, city, postal_code, department, latitude, longitude, distance_km, company_created_at, legal_category,
            company_size, employee_range, establishments_open, is_chain, excluded_reason, do_not_contact, status,
            geo_confidence, business_status_confidence, chain_kind, chain_confidence, revenue, revenue_year, manager_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                         (est.siren, est.siret, fp, est.name[:255], (est.trade_name or None), key, label, est.naf_code, (est.address or "")[:255],
                          est.city, est.postal_code, est.department, est.latitude, est.longitude, est.distance_km, est.created_at,
                          est.legal_category, est.company_size, est.employee_range, est.establishments_open, int(bool(reasons)),
                          excluded, int(dnc), "DO_NOT_CONTACT" if dnc else "DISCOVERED", geo_conf, 0.85, kind, 0.8 if reasons else None,
                          getattr(est, "revenue", None), getattr(est, "revenue_year", None), getattr(est, "manager_name", None)))
        created = True
    db.execute(conn, "INSERT IGNORE INTO local_prospect_campaigns (prospect_id, campaign_id) VALUES (%s,%s)", (pid, campaign_id))
    db.execute(conn, """INSERT INTO local_prospect_sources (prospect_id, source, source_ref) VALUES (%s,%s,%s)
                        ON DUPLICATE KEY UPDATE seen_at=UTC_TIMESTAMP(), source_ref=VALUES(source_ref)""", (pid, est.source, est.raw_ref or est.siret))
    return pid, created


def other_establishment(conn, siren: str | None, siret: str | None) -> bool:
    """Un AUTRE établissement de la même entreprise a-t-il déjà sa fiche ?"""
    if not siren:
        return False
    return db.fetch_one(conn, "SELECT 1 AS x FROM local_prospects WHERE siren=%s AND (siret IS NULL OR siret<>%s) LIMIT 1", (siren, siret or "")) is not None


def add_source(conn, pid: int, source: str, ref: str | None = None) -> None:
    db.execute(conn, """INSERT INTO local_prospect_sources (prospect_id, source, source_ref) VALUES (%s,%s,%s)
                        ON DUPLICATE KEY UPDATE seen_at=UTC_TIMESTAMP(), source_ref=VALUES(source_ref)""", (pid, source, ref))


def _j(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def save_resolution(conn, pid: int, res, socials: list[str] | None = None) -> None:
    """Enregistre le niveau de site, TOUTES les preuves (`websiteEvidence[]`), la couverture de la recherche et le domaine canonique."""
    ev = {"evidence": res.evidence, "candidates": res.candidates, "queries": res.queries, "strategies": res.strategies_tried,
          "socials": (res.socials if socials is None else socials)[:5], "degraded": res.degraded}
    db.execute(conn, """UPDATE local_prospects SET website_status=%s, website_url=%s, website_confidence=%s, website_evidence=%s,
                        website_original_url=%s, website_final_url=%s, canonical_domain=%s, redirect_chain=%s,
                        website_search_tried=%s, website_search_total=%s, website_absence_confidence=%s, website_checked_at=UTC_TIMESTAMP(),
                        enriched_at=UTC_TIMESTAMP(), status=IF(status IN ('DISCOVERED'), 'ENRICHED', status) WHERE id=%s""",
               (res.status, res.url, round(res.confidence, 2), _j(ev), res.original_url, res.final_url, res.canonical_domain, _j(res.redirect_chain or None),
                len(res.strategies_tried), res.strategies_total, res.absence_confidence, pid))
    add_source(conn, pid, "websearch", res.url or None)
    set_provenance(conn, pid, "website", {"source": "searxng", "strategies": res.strategies_tried, "status": res.status, "url": res.url,
                                          "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})


def set_provenance(conn, pid: int, key: str, value: dict) -> None:
    """« D'où vient cette donnée ? » : source, URL, date — une entrée par information importante."""
    db.execute(conn, "UPDATE local_prospects SET provenance = JSON_SET(COALESCE(provenance, JSON_OBJECT()), %s, CAST(%s AS JSON)) WHERE id=%s",
               ("$." + key, _j(value), pid))


def bad_domains(conn, siret: str | None, fingerprint: str | None) -> list[str]:
    rows = db.fetch_all(conn, "SELECT domain FROM local_bad_sites WHERE (siret IS NOT NULL AND siret=%s) OR (fingerprint IS NOT NULL AND fingerprint=%s)",
                        (siret, fingerprint))
    return [r["domain"] for r in rows]


def save_chain(conn, pid: int, ch) -> None:
    db.execute(conn, "UPDATE local_prospects SET chain_confidence=%s, chain_name=%s, chain_kind=%s, chain_evidence=%s, is_chain=%s WHERE id=%s",
               (round(ch.confidence, 2), ch.name, ch.kind, _j(ch.evidence), int(ch.is_chain), pid))


def save_osm(conn, pid: int, m: dict | None) -> None:
    db.execute(conn, "UPDATE local_prospects SET osm_data=%s WHERE id=%s", (_j(m), pid))
    if m:
        add_source(conn, pid, "osm", m.get("osm_id"))
        set_provenance(conn, pid, "osm", {"source": "openstreetmap", "id": m.get("osm_id"), "distance_m": m.get("distance_m"), "confidence": m.get("confidence")})


def chain_context(conn, row: dict) -> dict:
    """Ce que la base sait déjà : même enseigne ailleurs, autres établissements du même SIREN, même domaine sous un autre SIREN."""
    trade = fold(row.get("trade_name") or row.get("company_name"))
    same_trade = 0
    if trade and len(trade) >= 4:
        same_trade = db.fetch_one(conn, """SELECT COUNT(*) AS n FROM local_prospects WHERE id <> %s AND (siren IS NULL OR siren <> %s)
                                           AND LOWER(COALESCE(trade_name, company_name)) = LOWER(%s)""", (row["id"], row.get("siren") or "", row.get("trade_name") or row["company_name"]))["n"]
    siblings = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE siren=%s AND id <> %s", (row.get("siren"), row["id"]))["n"] if row.get("siren") else 0
    shared = 0
    if row.get("canonical_domain"):
        shared = db.fetch_one(conn, """SELECT COUNT(DISTINCT siren) AS n FROM local_prospects WHERE canonical_domain=%s AND id <> %s AND siren <> %s
                                       AND website_status IN ('CONFIRMED','PROBABLE')""", (row["canonical_domain"], row["id"], row.get("siren") or ""))["n"]
    return {"same_trade_elsewhere": same_trade, "same_siren_siblings": siblings, "shared_domain_siren": shared}


def refresh_siblings(conn, siren: str | None) -> None:
    """Niveau COMPANY / ESTABLISHMENT : une PME à plusieurs établissements a UNE fiche principale (siège, sinon la plus ancienne)."""
    if not siren:
        return
    rows = db.fetch_all(conn, "SELECT id FROM local_prospects WHERE siren=%s ORDER BY id", (siren,))
    n = len(rows)
    for i, r in enumerate(rows):
        db.execute(conn, "UPDATE local_prospects SET sibling_count=%s, entity_level=%s WHERE id=%s", (n - 1, "COMPANY" if (n > 1 and i == 0) else "ESTABLISHMENT", r["id"]))


# ── Pipeline : étape, erreurs, reprise ───────────────────────────────────────────────────────────────────
def set_stage(conn, pid: int, stage: str) -> None:
    db.execute(conn, "UPDATE local_prospects SET pipeline_stage=%s WHERE id=%s", (stage, pid))


def mark_error(conn, pid: int, category: str, detail: str) -> int | None:
    """Enregistre l'erreur, incrémente les tentatives et planifie la reprise (backoff). Renvoie le délai en minutes ou None (abandon)."""
    from . import errors
    row = db.fetch_one(conn, "SELECT attempts FROM local_prospects WHERE id=%s", (pid,))
    attempts = int(row["attempts"] or 0) + 1 if row else 1
    delay = errors.retry_delay_minutes(category, attempts)
    db.execute(conn, """UPDATE local_prospects SET pipeline_stage='ERROR', attempts=%s, error_category=%s, last_error=%s,
                        next_retry_at=IF(%s IS NULL, NULL, UTC_TIMESTAMP() + INTERVAL %s MINUTE) WHERE id=%s""",
               (attempts, category, detail[:250], delay, delay or 0, pid))
    return delay


def clear_error(conn, pid: int, stage: str = "DONE") -> None:
    db.execute(conn, "UPDATE local_prospects SET pipeline_stage=%s, attempts=0, error_category=NULL, last_error=NULL, next_retry_at=NULL WHERE id=%s", (stage, pid))


def save_audit(conn, pid: int, audit) -> None:
    db.execute(conn, """UPDATE local_prospects SET technical_score=%s, seo_score=%s, seo_opportunity_score=%s, performance_score=%s,
                        modernization_opportunity=%s, modernization_evidence=%s, issues=%s, audit=%s, seo_evidence=%s, performance_status=%s,
                        local_presence=%s, audited_at=UTC_TIMESTAMP(),
                        status=IF(status IN ('DISCOVERED','ENRICHED'), 'AUDITED', status) WHERE id=%s""",
               (audit.technical_score, audit.seo_score, audit.seo_opportunity_score, audit.performance_score, audit.modernization,
                _j(audit.modernization_evidence), _j([i.__dict__ for i in audit.issues]), _j(audit.as_dict()), _j(audit.seo_evidence),
                audit.performance_status, _j(audit.local_presence or None), pid))


def save_contacts(conn, pid: int, c: dict) -> None:
    if not any(c.get(k) for k in ("phone", "email", "contact_page", "contact_form")):
        return
    db.execute(conn, """UPDATE local_prospects SET phone=%s, phone_confidence=%s, email=%s, email_kind=%s, contact_form=%s, contact_page=%s,
                        contact_source_url=%s, contact_confidence=%s, contact_evidence=%s, contact_discovered_at=UTC_TIMESTAMP() WHERE id=%s""",
               (c.get("phone"), c.get("phone_confidence"), c.get("email"), c.get("email_kind"), int(bool(c.get("contact_form"))), c.get("contact_page"),
                c.get("contact_source_url"), c.get("contact_confidence"), _j(c.get("evidence") or None), pid))
    for e in c.get("evidence") or []:
        if e["kind"] in ("phone", "email"):
            set_provenance(conn, pid, e["kind"], {"source": e.get("source", "site officiel"), "via": e.get("via"), "url": e.get("url"), "confidence": e.get("confidence")})


def save_socials(conn, pid: int, links: list[dict]) -> None:
    """Profils sociaux VÉRIFIÉS (`socials.py`) : liste vide = cherché, rien de sûr trouvé (≠ None : pas encore cherché)."""
    db.execute(conn, "UPDATE local_prospects SET social_links=%s WHERE id=%s", (_j(links), pid))


def add_contacts(conn, pid: int, row: dict, extra: dict) -> list[str]:
    """Complète les coordonnées MANQUANTES avec `extra` (même forme que `contacts.extract`), sans jamais remplacer une coordonnée connue.
    Renvoie les champs ajoutés."""
    added, sets, vals = [], [], []
    if not row.get("phone") and extra.get("phone"):
        sets += ["phone=%s", "phone_confidence=%s"]; vals += [extra["phone"], extra.get("phone_confidence")]; added.append("phone")
    if not row.get("email") and extra.get("email"):
        sets += ["email=%s", "email_kind=%s"]; vals += [extra["email"], extra.get("email_kind")]; added.append("email")
    if not added:
        return []
    old = row.get("contact_evidence")
    old = json.loads(old) if isinstance(old, (str, bytes)) else (old or [])
    ev = old + [e for e in extra.get("evidence") or [] if e["kind"] in added]
    confs = [e["confidence"] for e in ev if e.get("kind") in ("phone", "email", "form") and e.get("confidence") is not None]
    sets += ["contact_evidence=%s", "contact_confidence=%s", "contact_source_url=COALESCE(contact_source_url, %s)", "contact_discovered_at=UTC_TIMESTAMP()"]
    vals += [_j(ev), round(min(0.98, max(confs) + (0.04 if len(confs) > 1 else 0.0)), 2) if confs else None, extra.get("contact_source_url")]
    db.execute(conn, f"UPDATE local_prospects SET {', '.join(sets)} WHERE id=%s", (*vals, pid))
    for e in extra.get("evidence") or []:
        if e["kind"] in added:
            set_provenance(conn, pid, e["kind"], {"source": e.get("source", "extrait de recherche"), "via": e.get("via"), "url": e.get("url"), "confidence": e.get("confidence")})
    return added


def save_lighthouse(conn, pid: int, r: dict) -> None:
    db.execute(conn, """UPDATE local_prospects SET lighthouse_performance=%s, lighthouse_seo=%s, lighthouse_accessibility=%s,
                        lighthouse_best_practices=%s, lighthouse_at=UTC_TIMESTAMP() WHERE id=%s""",
               (r["performance"], r["seo"], r["accessibility"], r["best_practices"], pid))


def save_score(conn, pid: int, s: dict) -> None:
    """Le brouillon (`draft_message`) n'est JAMAIS touché ici : il n'existe que si l'utilisateur l'a demandé depuis la fiche."""
    db.execute(conn, """UPDATE local_prospects SET prospect_score=%s, category=%s, score_stage=%s, score_details=%s, confidence=%s,
                        commercial_potential_score=%s, data_confidence_score=%s, score_raw=%s, buy_signals=%s, budget_level=%s,
                        scored_at=UTC_TIMESTAMP(),
                        status=IF(status IN ('DISCOVERED','ENRICHED','AUDITED','QUALIFIED'),
                                  IF(%s IN ('TRES_BON','A_CONTACTER','A_EXAMINER') AND %s = 'FINAL', 'QUALIFIED', status), status)
                        WHERE id=%s""",
               (s["score"], s["category"], s["stage"],
                _j({"details": s["details"], "summary": s["summary"], "raw": s["raw"], "caps": s["caps"], "data_confidence_details": s["data_confidence_details"]}),
                s["confidence"], s["commercial_potential"], s["data_confidence"], s["raw"], _j(s.get("signals") or []), s.get("budget_level"),
                s["category"] or "", s["stage"], pid))


def row_for_scoring(row: dict) -> dict:
    """Ligne SQL → dict attendu par `scoring.score_prospect` (JSON décodé, dates et décimaux normalisés)."""
    p = dict(row)
    for k in ("bodacc", "website_evidence", "issues", "audit", "modernization_evidence", "score_details", "contact_evidence", "chain_evidence", "osm_data", "provenance"):
        if isinstance(p.get(k), (str, bytes)):
            p[k] = json.loads(p[k])
    ev = p.get("website_evidence") or {}
    links = json.loads(p["social_links"]) if isinstance(p.get("social_links"), (str, bytes)) else p.get("social_links")
    p["socials"] = [x["url"] for x in links] if links is not None else (ev.get("socials") or [])      # vérifiés (anciennes fiches : non vérifiés)
    for k in ("distance_km", "website_confidence", "revenue"):
        if p.get(k) is not None:
            p[k] = float(p[k])
    return p


def now() -> datetime:
    return datetime.now(timezone.utc)
