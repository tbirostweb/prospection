"""Nouvelle recherche d'e-mail pour les prospects déjà traités qui n'en ont pas :  python -m worker.local.emailsearch   (deux fois par heure)

Une fiche n'est vérifiée qu'une fois ; une adresse publiée plus tard (page Facebook, annuaire, mentions légales) ne serait jamais vue. Ici, à
chaque passage, LOCAL_EMAIL_SEARCH_PER_RUN prospects sans e-mail (jamais contactés, hors « ne plus contacter » et écartés) reçoivent UNE recherche
dédiée (`runner.email_search`), au plus une fois tous les EMAIL_RECHECK_DAYS jours. Une adresse trouvée complète la fiche (jamais elle ne
remplace une coordonnée connue) et le score est recalculé : la fiche peut alors passer « À contacter » et partir par l'envoi automatique.
Même verrou que les campagnes (jamais en même temps) et même budget de moteurs. Avant les e-mails, la nouvelle recherche des sites ratés
(`siterecheck`) : un site trouvé apporte ses propres coordonnées.
"""
from __future__ import annotations

import json
import sys

from .. import db
from ..config import env, log
from . import engines, runner, store
from .errors import SearchUnavailable

PER_RUN = int(env("LOCAL_EMAIL_SEARCH_PER_RUN", "10") or 10)
EMAIL_RECHECK_DAYS = runner.EMAIL_RECHECK_DAYS


def candidates(conn, limit: int) -> list[dict]:
    """Prospects traités sans e-mail, jamais cherchés (ou pas depuis EMAIL_RECHECK_DAYS jours) : ceux dont le message est déjà rédigé d'abord
    (une adresse trouvée les rend envoyables aussitôt), puis les plus prometteurs."""
    return db.fetch_all(conn, f"""SELECT p.* FROM local_prospects p
        WHERE (p.email IS NULL OR p.email = '') AND p.do_not_contact = 0 AND p.contacted_at IS NULL AND p.excluded_reason IS NULL
          AND p.is_chain = 0 AND p.pipeline_stage = 'DONE' AND (p.category IS NULL OR p.category <> 'IGNORER')
          AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT')
          AND (JSON_EXTRACT(p.provenance, '$.email_search.checked_at') IS NULL
               OR STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.email_search.checked_at')), '%%Y-%%m-%%dT%%H:%%i:%%sZ')
                  < UTC_TIMESTAMP() - INTERVAL {EMAIL_RECHECK_DAYS} DAY)
        ORDER BY (p.draft_message IS NOT NULL) DESC, FIELD(p.category, 'A_EXAMINER', 'A_CONTACTER', 'TRES_BON') DESC, p.prospect_score DESC, p.id
        LIMIT %s""", (limit,))


def _rescore(conn, pid: int) -> None:
    """Score recalculé avec les réglages de la campagne la plus récente du prospect (une adresse trouvée peut le faire passer « À contacter »)."""
    camp = db.fetch_one(conn, """SELECT c.* FROM local_campaigns c JOIN local_prospect_campaigns pc ON pc.campaign_id = c.id
                                 WHERE pc.prospect_id = %s ORDER BY c.id DESC LIMIT 1""", (pid,))
    if camp:
        runner._rescore(conn, db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,)), runner.Ctx(conn, camp))


def rescore_manual(conn) -> int:
    """Adresses ajoutées à la main dans l'app (provenance `email_manual.pending`) : score recalculé, puis le drapeau est levé."""
    rows = db.fetch_all(conn, """SELECT id FROM local_prospects
                                 WHERE JSON_UNQUOTE(JSON_EXTRACT(provenance, '$.email_manual.pending')) = 'true' LIMIT 200""")
    for r in rows:
        _rescore(conn, r["id"])
        db.execute(conn, "UPDATE local_prospects SET provenance = JSON_SET(provenance, '$.email_manual.pending', FALSE) WHERE id=%s", (r["id"],))
    conn.commit()
    return len(rows)


def run(conn, search, limit: int = PER_RUN) -> dict:
    stats = {"searched": 0, "found": 0, "manual_rescored": rescore_manual(conn)}
    if limit <= 0:
        return stats
    for row in candidates(conn, limit):
        company = {**runner._company(conn, row), "department": row.get("department")}
        try:
            found = runner.email_search(conn, row["id"], company, search, row)
        except SearchUnavailable:
            break                                              # moteurs indisponibles : on s'arrête, la suite au prochain passage
        if found is None:
            continue
        stats["searched"] += 1
        if found["email"] and not store.is_do_not_contact(conn, row.get("siret"), row.get("siren"), email=found["email"]):
            if store.add_contacts(conn, row["id"], row, found):
                stats["found"] += 1
                _rescore(conn, row["id"])                      # contact trouvé : la fiche peut devenir « À contacter »
        conn.commit()
    return stats


def main() -> int:
    lock = runner._acquire_lock()
    if lock is None:
        return 0                                               # une campagne tourne : elle a la priorité sur les moteurs
    try:
        with db.connect() as conn:
            pool = engines.SearchPool(conn)
            try:
                from . import naflabels
                labels = naflabels.backfill(conn)                  # fiches sans métier : intitulé officiel du code NAF (borné, idempotent)
                if labels["examined"]:
                    log.info("[local] métiers d'après le code NAF : %s", json.dumps(labels))
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                log.warning("[local] métiers d'après le code NAF en échec : %s", exc)
            try:
                from . import siterecheck
                sites = siterecheck.run(conn, pool)
                if sites["rechecked"]:
                    log.info("[local] nouvelle recherche de sites : %s", json.dumps(sites))
                lost = siterecheck.places_site_check(conn)
                if lost["asked"]:
                    log.info("[local] sites de fiche Google redemandés (Place Details) : %s", json.dumps(lost))
                places_stats = siterecheck.places_backfill(conn)
                if places_stats["asked"]:
                    log.info("[local] Google Places sur les fiches déjà trouvées : %s", json.dumps(places_stats))
            except Exception as exc:  # noqa: BLE001 — n'empêche jamais la recherche d'e-mails
                conn.rollback()
                log.warning("[local] nouvelle recherche de sites en échec : %s", exc)
            stats = run(conn, pool)
        if stats["searched"] or stats["manual_rescored"]:
            log.info("[local] recherche d'e-mails : %s", json.dumps(stats))
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
