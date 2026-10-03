"""Retours de classement sur un prospect (Telegram et tests) — même logique que l'API web : statut, raison du 🚫, « Mauvais site ».

Un statut posé ici est MANUEL : le worker ne l'écrase jamais (`store.AUTO_STATUSES`). « Ne plus contacter » n'est jamais modifiable depuis ce module.
"""
from __future__ import annotations

import json

from .. import db

REASONS = {"not_relevant", "site_ok", "wrong_site", "chain", "agency_client", "no_need", "no_contact", "other"}
SITE_COLUMNS_RESET = """website_status=NULL, website_url=NULL, website_confidence=NULL, website_evidence=NULL, website_original_url=NULL, website_final_url=NULL,
  canonical_domain=NULL, redirect_chain=NULL, website_search_tried=NULL, website_search_total=NULL, website_absence_confidence=NULL, phone=NULL,
  phone_confidence=NULL, email=NULL, email_kind=NULL, contact_form=0, contact_page=NULL, contact_source_url=NULL, contact_confidence=NULL, contact_evidence=NULL,
  technical_score=NULL, seo_score=NULL, seo_opportunity_score=NULL, performance_score=NULL, performance_status=NULL, modernization_opportunity=NULL,
  modernization_evidence=NULL, seo_evidence=NULL, local_presence=NULL, issues=NULL, audit=NULL, audited_at=NULL, lighthouse_performance=NULL, lighthouse_seo=NULL,
  lighthouse_accessibility=NULL, lighthouse_best_practices=NULL, lighthouse_at=NULL, chain_confidence=NULL, chain_kind=NULL, chain_name=NULL, chain_evidence=NULL,
  is_chain=0, draft_message=NULL, draft_subject=NULL, draft_created_at=NULL, provenance=NULL, pipeline_stage='DISCOVERED', attempts=0, next_retry_at=NULL, error_category=NULL, last_error=NULL"""


def _get(conn, pid: int) -> dict | None:
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))


def star(conn, pid: int) -> str:
    p = _get(conn, pid)
    if not p:
        return "Prospect introuvable"
    if p["do_not_contact"]:
        return "Ne plus contacter : inchangé"
    db.execute(conn, "UPDATE local_prospects SET status='TO_CONTACT' WHERE id=%s", (pid,))
    conn.commit()
    return "⭐ Marqué « à contacter »"


def called(conn, pid: int) -> str:
    p = _get(conn, pid)
    if not p:
        return "Prospect introuvable"
    if p["do_not_contact"]:
        return "Ne plus contacter : inchangé"
    db.execute(conn, """UPDATE local_prospects SET status='CONTACTED', contacted_at=COALESCE(contacted_at, UTC_TIMESTAMP()), last_contacted_at=UTC_TIMESTAMP() WHERE id=%s""", (pid,))
    conn.commit()
    return "📞 Marqué contacté"


def dislike(conn, pid: int, reason: str | None = None) -> str:
    p = _get(conn, pid)
    if not p:
        return "Prospect introuvable"
    if p["do_not_contact"]:
        return "Ne plus contacter : inchangé"
    if reason is not None and reason not in REASONS:
        return "Raison invalide"
    db.execute(conn, "UPDATE local_prospects SET status='LOST', response_status='NOT_A_FIT', feedback_reason=COALESCE(%s, feedback_reason) WHERE id=%s", (reason, pid))
    conn.commit()
    return "🚫 Écarté" + (" (raison enregistrée)" if reason else "")


def wrong_site(conn, pid: int) -> str:
    """Dissocie le site (jamais reproposé pour CE SIRET), retire tout ce qui en venait (contacts, audit, chaîne, score) et relance la recherche."""
    p = _get(conn, pid)
    if not p:
        return "Prospect introuvable"
    if p["do_not_contact"]:
        return "Ne plus contacter : inchangé"
    domain = p.get("canonical_domain")
    if not domain and p.get("website_url"):
        from urllib.parse import urlparse
        domain = (urlparse(p["website_url"]).hostname or "").removeprefix("www.") or None
    if not domain:
        return "Aucun site associé à dissocier"
    snap = {k: (str(p[k]) if p.get(k) is not None else None) for k in ("company_name", "trade_name", "siret", "siren", "city", "postal_code", "address", "naf_code")}
    snap.update(previous_site=p.get("website_url"), previous_status=p.get("website_status"), previous_confidence=str(p.get("website_confidence")))
    db.execute(conn, "INSERT IGNORE INTO local_bad_sites (siret, fingerprint, domain, reason) VALUES (%s,%s,%s,%s)", (p["siret"], p["fingerprint"], domain, "mauvais site (retour utilisateur)"))
    db.execute(conn, "INSERT INTO local_site_feedback (prospect_id, siret, kind, url, company) VALUES (%s,%s,'wrong_site',%s,%s)", (pid, p["siret"], p.get("website_url"), json.dumps(snap, ensure_ascii=False)))
    db.execute(conn, f"UPDATE local_prospects SET {SITE_COLUMNS_RESET} WHERE id=%s", (pid,))
    db.execute(conn, "UPDATE local_campaigns SET status='queued', enabled=1 WHERE id IN (SELECT campaign_id FROM local_prospect_campaigns WHERE prospect_id=%s)", (pid,))
    conn.commit()
    return "❌ Site dissocié : recherche relancée"
