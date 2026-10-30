"""Rattrapage de l'audit : refait UNE fois l'audit des fiches déjà auditées avant que l'audit relève les défauts concrets cités dans les messages
(coordonnées absentes de l'accueil, générateur de site ancien…). Les autres défauts (téléphone, HTTPS, lenteur, © ancien, technologies
obsolètes) sont déjà stockés et lus tels quels : seules les fiches vraiment concernées par ces nouveaux relevés sont reprises.

Borné : LOCAL_AUDIT_REFRESH_PER_RUN fiches par passage (0 = désactivé), jamais contactées ni en séquence, hors « ne plus contacter » / exclues /
chaînes, site CONFIRMED / PROBABLE. Une fois par fiche : provenance `audit_refresh`. Une fiche qui échoue est marquée aussi (pas de boucle).
Appelé par `siterecheck.main` après la revérification des sites (aucune requête moteur : 4 GET ordinaires par fiche)."""
from __future__ import annotations

import time

from .. import db
from ..config import env, log
from . import audit as audit_mod, runner, store

PER_RUN = int(env("LOCAL_AUDIT_REFRESH_PER_RUN", "10") or 0)
AUDIT_VERSION = 2            # 2 = relève no_contact_visible et le générateur de site ancien

CANDIDATES = """SELECT p.* FROM local_prospects p
    WHERE p.audited_at IS NOT NULL AND p.website_status IN ('CONFIRMED','PROBABLE') AND p.website_url IS NOT NULL
      AND p.contacted_at IS NULL AND (p.auto_state IS NULL OR p.auto_state = 'queued')
      AND p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND p.pipeline_stage = 'DONE'
      AND (p.category IS NULL OR p.category <> 'IGNORER') AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT','CONTACTED')
      AND JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.audit_refresh') IS NULL
    ORDER BY (p.draft_message IS NOT NULL) DESC, p.prospect_score DESC, p.id
    LIMIT %s"""


def run(conn, fetch=None, limit: int = PER_RUN, deadline: float | None = None) -> dict:
    stats = {"refreshed": 0, "failed": 0, "stop": "off"}
    if limit <= 0:
        return stats
    if fetch is None:
        from . import fetch as fetch_mod
        fetch = fetch_mod.Fetcher()
    rows = db.fetch_all(conn, CANDIDATES, (limit,))
    stats["stop"] = "limit" if len(rows) >= limit else "nothing"
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            stats["stop"] = "time"
            break
        pid = row["id"]
        try:
            a = audit_mod.audit_site(row["website_url"], fetch)
            if a.reachable:                                    # site muet à cet instant : l'ancien audit est gardé (rien d'effacé)
                store.save_audit(conn, pid, a)
                from . import siterecheck
                row2 = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
                runner._rescore(conn, row2, siterecheck.ctx_for(conn, pid))
            store.set_provenance(conn, pid, "audit_refresh", {"checked_at": runner._now_iso(), "version": AUDIT_VERSION, "reachable": a.reachable})
            stats["refreshed" if a.reachable else "failed"] += 1
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête pas les autres ; marquée pour ne pas boucler
            conn.rollback()
            log.warning("[local] rattrapage de l'audit #%s en échec : %s", pid, exc)
            store.set_provenance(conn, pid, "audit_refresh", {"checked_at": runner._now_iso(), "error": str(exc)[:160]})
            stats["failed"] += 1
        conn.commit()
    return stats
