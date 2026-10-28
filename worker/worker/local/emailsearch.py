"""Enrichissement des fiches existantes :  python -m worker.local.emailsearch [--no-engines]   (voir `enrich.main` : ordre, verrous, budgets, état)

Une fiche n'est vérifiée qu'une fois ; une adresse publiée plus tard (page Facebook, annuaire, mentions légales) ne serait jamais vue. Ici, à
chaque passage, LOCAL_EMAIL_SEARCH_PER_RUN prospects sans e-mail (jamais contactés, hors « ne plus contacter » et écartés) reçoivent une recherche
dédiée (`runner.email_search`, plusieurs requêtes, arrêt dès une adresse), au plus une fois tous les EMAIL_RECHECK_MISS_DAYS jours (rien trouvé) ou EMAIL_RECHECK_DAYS. Une adresse trouvée complète la fiche (jamais elle ne
remplace une coordonnée connue) et le score est recalculé : la fiche peut alors passer « À contacter » et partir par l'envoi automatique.
Ordre : brouillon déjà rédigé, puis site vérifié sans e-mail (son site a été relu par `enrich.site_emails`), puis fiche avec téléphone (complète
« téléphone » : l'e-mail la rend envoyable), puis les plus prometteuses. Utilise les moteurs : sous le verrou des campagnes (`enrich.main`), même
budget de moteurs, borné (`limit`, `deadline`).
"""
from __future__ import annotations

import sys
import time

from .. import db
from ..config import env
from . import runner, store
from .errors import SearchUnavailable

PER_RUN = int(env("LOCAL_EMAIL_SEARCH_PER_RUN", "40") or 40)
EMAIL_RECHECK_DAYS = runner.EMAIL_RECHECK_DAYS
EMAIL_RECHECK_MISS_DAYS = runner.EMAIL_RECHECK_MISS_DAYS


def candidates(conn, limit: int) -> list[dict]:
    """Prospects traités sans e-mail, jamais cherchés (ou pas depuis EMAIL_RECHECK_DAYS jours) : ceux dont le message est déjà rédigé d'abord
    (une adresse trouvée les rend envoyables aussitôt), puis site vérifié, puis téléphone connu, puis les plus prometteurs."""
    return db.fetch_all(conn, f"""SELECT p.* FROM local_prospects p
        WHERE (p.email IS NULL OR p.email = '') AND p.do_not_contact = 0 AND p.contacted_at IS NULL AND p.excluded_reason IS NULL
          AND p.is_chain = 0 AND p.pipeline_stage = 'DONE' AND (p.category IS NULL OR p.category <> 'IGNORER')
          AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT')
          AND (JSON_EXTRACT(p.provenance, '$.email_search.checked_at') IS NULL
               OR STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.email_search.checked_at')), '%%Y-%%m-%%dT%%H:%%i:%%sZ')
                  < UTC_TIMESTAMP() - INTERVAL IF(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.email_search.found')) = 'true',
                                                  {EMAIL_RECHECK_DAYS}, {EMAIL_RECHECK_MISS_DAYS}) DAY)
        ORDER BY (p.draft_message IS NOT NULL) DESC, (p.website_status IN ('CONFIRMED','PROBABLE')) DESC, (TRIM(COALESCE(p.phone, '')) <> '') DESC,
                 FIELD(p.category, 'A_EXAMINER', 'A_CONTACTER', 'TRES_BON') DESC, p.prospect_score DESC, p.id
        LIMIT %s""", (limit,))


def _rescore(conn, pid: int) -> None:
    """Score recalculé avec les réglages de la campagne la plus récente du prospect (une adresse trouvée peut le faire passer « À contacter »)."""
    from . import siterecheck
    runner._rescore(conn, db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,)), siterecheck.ctx_for(conn, pid))   # fiche importée : réglages généraux


def rescore_manual(conn) -> int:
    """Adresses ajoutées à la main dans l'app (provenance `email_manual.pending`) : score recalculé, puis le drapeau est levé."""
    rows = db.fetch_all(conn, """SELECT id FROM local_prospects
                                 WHERE JSON_UNQUOTE(JSON_EXTRACT(provenance, '$.email_manual.pending')) = 'true' LIMIT 200""")
    for r in rows:
        _rescore(conn, r["id"])
        db.execute(conn, "UPDATE local_prospects SET provenance = JSON_SET(provenance, '$.email_manual.pending', FALSE) WHERE id=%s", (r["id"],))
    conn.commit()
    return len(rows)


def run(conn, search, limit: int = PER_RUN, deadline: float | None = None, manual: bool = True) -> dict:
    stats = {"searched": 0, "found": 0, "manual_rescored": rescore_manual(conn) if manual else 0, "stop": "off"}
    if limit <= 0:
        return stats
    rows = candidates(conn, limit)
    stats["stop"] = "limit" if len(rows) >= limit else "nothing"
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            stats["stop"] = "time"
            break
        company = {**runner._company(conn, row), "department": row.get("department")}
        try:
            found = runner.email_search(conn, row["id"], company, search, row, deadline=deadline)
        except SearchUnavailable:
            conn.rollback()
            stats["stop"] = "engines"
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


def main(argv: list[str] | None = None) -> int:
    from . import enrich
    return enrich.main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
