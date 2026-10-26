"""Nouvelle recherche du SITE des prospects classés « sans site » :  appelée par `python -m worker.local.emailsearch` (deux fois par heure)

Les devinettes de domaine (nom collé, nom + département, domaine de l'adresse e-mail) et la reconnaissance du nom dans le domaine ont été
améliorées après coup : des fiches déjà en base sont restées NOT_FOUND / UNCERTAIN alors que leur site existe (constaté : METALEX →
metalex-10.com, KARROSS PERFORMANCE → karrossperformance.fr). Leur brouillon dit « je n'ai pas trouvé de site » : il serait faux.

À chaque passage, LOCAL_SITE_RECHECK_PER_RUN fiches (jamais contactées, hors « ne plus contacter », écartées et chaînes, pas encore revérifiées :
provenance `site_recheck`) — celles qui ont déjà un brouillon d'abord — reçoivent UNE nouvelle résolution (`sitefinder.resolve`), devinettes
d'abord (aucune requête), puis au plus RECHECK_STRATEGIES recherches (les autres ont déjà été faites, sans résultat). Site CONFIRMED / PROBABLE :
  * la résolution est enregistrée comme par le runner (`store.save_resolution`) ;
  * les contacts lus sur le site sont AJOUTÉS (`store.add_contacts`) : un téléphone ou un e-mail déjà connu n'est jamais effacé ni remplacé ;
  * la suite du traitement est celle du runner (`runner.process_prospect`, avec les pages déjà lues) : site_year, « ne plus contacter » par
    domaine, exclusion, audit (s'il est actif), réseaux sociaux, chaîne, score. Elle ne réécrit pas les contacts : la provenance `contacts`
    est posée AVANT ;
  * le brouillon « sans site » est effacé et la fiche sort de la file d'envoi (auto_state 'queued' → NULL).
Un prospect contacté (contacted_at) ou en séquence (auto_state 'active') n'est jamais touché, même entre la sélection et l'écriture (conditions
répétées dans les UPDATE). Sinon (toujours rien de sûr) : seule la provenance `site_recheck` est posée, l'ancienne résolution reste.
Même verrou que les campagnes (appelé sous le verrou de `emailsearch.main`).
"""
from __future__ import annotations

import json
import sys

from .. import db
from ..config import env, log
from . import contacts, engines, runner, sitefinder, socials as socials_mod, store
from .errors import SearchUnavailable

PER_RUN = int(env("LOCAL_SITE_RECHECK_PER_RUN", "10") or 10)
PLACES_PER_RUN = int(env("LOCAL_PLACES_BACKFILL_PER_RUN", "1") or 1)    # 2 passages/heure × 13 h ≈ 26 fiches/jour, sous le quota Google de 30/jour
PLACES_SETTING = "local_places_backfill"
RECHECK_STRATEGIES = 2                 # recherches au plus par fiche : nom + ville, puis la suivante (les 6 d'origine n'avaient rien donné)
UNTOUCHABLE = "p.contacted_at IS NULL AND (p.auto_state IS NULL OR p.auto_state = 'queued')"


NO_SITE_FILTER = f"""p.website_status IN ('NOT_FOUND','UNCERTAIN') AND {UNTOUCHABLE}
          AND p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND p.pipeline_stage = 'DONE'
          AND (p.category IS NULL OR p.category <> 'IGNORER') AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT','CONTACTED')
          AND EXISTS (SELECT 1 FROM local_prospect_campaigns pc WHERE pc.prospect_id = p.id)"""   # critères communs à la revérification et au passage Google Places
NO_PLACES = "JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.places') IS NULL"


def candidates(conn, limit: int, where: str = "JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.site_recheck') IS NULL") -> list[dict]:
    """Fiches « sans site » jamais revérifiées et jamais contactées : brouillon déjà rédigé d'abord (il serait faux), puis les plus prometteuses.
    `where` : condition de provenance en plus des critères communs (par défaut : pas encore revérifiée ; le passage Google Places pose NO_PLACES)."""
    return db.fetch_all(conn, f"""SELECT p.* FROM local_prospects p
        WHERE {NO_SITE_FILTER} AND {where}
        ORDER BY (p.draft_message IS NOT NULL) DESC, FIELD(p.category, 'A_EXAMINER', 'A_CONTACTER', 'TRES_BON') DESC, p.prospect_score DESC, p.id
        LIMIT %s""", (limit,))


def _campaign(conn, pid: int) -> dict | None:
    return db.fetch_one(conn, """SELECT c.* FROM local_campaigns c JOIN local_prospect_campaigns pc ON pc.campaign_id = c.id
                                 WHERE pc.prospect_id = %s ORDER BY c.id DESC LIMIT 1""", (pid,))


def _site_contacts(conn, row: dict, res, fetch) -> tuple[list, list[str]]:
    """Contacts lus sur le site trouvé, AJOUTÉS à ceux déjà connus (jamais remplacés). Renvoie (pages lues, champs ajoutés)."""
    pid = row["id"]
    pages = contacts.discover(res.pages or [], fetch, res.canonical_domain or "")
    c = contacts.extract(pages, res.canonical_domain or "")
    if c.get("email") and store.is_do_not_contact(conn, row.get("siret"), row.get("siren"), email=c["email"]):
        c["email"], c["email_kind"] = None, None                       # adresse opposée : jamais enregistrée
    added = store.add_contacts(conn, pid, row, c)
    if c.get("contact_page") or c.get("contact_form"):                # page / formulaire de contact : seulement s'il n'y en avait pas
        db.execute(conn, """UPDATE local_prospects SET contact_page=COALESCE(contact_page, %s), contact_form=GREATEST(COALESCE(contact_form, 0), %s)
                            WHERE id=%s""", (c.get("contact_page"), int(bool(c.get("contact_form"))), pid))
    prov = row.get("provenance")
    prov = json.loads(prov) if isinstance(prov, (str, bytes)) else (prov or {})
    if "contacts" not in prov:                                         # posée AVANT process_prospect : il ne réécrit jamais les contacts
        store.set_provenance(conn, pid, "contacts", {"checked_at": runner._now_iso(), "found": bool(added or row.get("phone") or row.get("email")),
                                                     "via": ["site_recheck"]})
    return pages, added


def _drop_no_site_draft(conn, pid: int) -> bool:
    """Brouillon « je n'ai pas trouvé de site » effacé, fiche retirée de la file d'envoi. Jamais sur une fiche contactée ou en séquence."""
    with conn.cursor() as cur:
        n = cur.execute(f"""UPDATE local_prospects p SET p.draft_message=NULL, p.draft_subject=NULL, p.draft_kind=NULL, p.draft_created_at=NULL,
                                p.auto_queued_at=IF(p.auto_state='queued', NULL, p.auto_queued_at), p.auto_state=IF(p.auto_state='queued', NULL, p.auto_state)
                            WHERE p.id=%s AND {UNTOUCHABLE} AND (p.draft_message IS NOT NULL OR p.auto_state='queued')""", (pid,))
    return bool(n)


def recheck(conn, row: dict, search, fetch) -> tuple[str, bool]:
    """UNE nouvelle résolution pour ce prospect. Renvoie (niveau trouvé, site enregistré ?). SearchUnavailable remonte (rien n'est écrit)."""
    ctx = runner.Ctx(conn, _campaign(conn, row["id"]))
    company = runner._company(conn, row)
    res = sitefinder.resolve(company, search, fetch, ctx.site_thresholds, strategy_budget=RECHECK_STRATEGIES,
                             known_urls=runner._known_urls(conn, row), dns=ctx.dns,
                             candidate_urls=runner._places_urls(conn, row))
    return _conclude(conn, row, ctx, company, res, fetch)


def _conclude(conn, row: dict, ctx, company: dict, res, fetch, only_if_found: bool = False) -> tuple[str, bool]:
    """Suite d'une résolution (revérification ou passage Google Places) : site accepté = enregistré + contacts ajoutés + traitement du runner +
    brouillon « sans site » effacé. `only_if_found` : sans site sûr, aucune provenance `site_recheck` n'est posée (la fiche n'a pas été revérifiée)."""
    pid = row["id"]
    flag = {"checked_at": runner._now_iso(), "previous": row.get("website_status"), "status": res.status, "confidence": round(res.confidence, 2),
            "url": res.url if res.accepted else None}
    fresh = db.fetch_one(conn, f"SELECT p.* FROM local_prospects p WHERE p.id=%s AND {UNTOUCHABLE}", (pid,))
    if not res.accepted or fresh is None:                             # rien de sûr (ou contacté entre-temps) : l'ancienne résolution reste
        if not only_if_found:
            store.set_provenance(conn, pid, "site_recheck", flag)
        return res.status, False
    known = fresh.get("social_links")
    known = json.loads(known) if isinstance(known, (str, bytes)) else (known or [])
    found = socials_mod.from_hits({**company, "manager_name": fresh.get("manager_name")}, res.hits)
    store.save_resolution(conn, pid, res, socials=[x["url"] for x in socials_mod.merge(known, found)])
    store.save_socials(conn, pid, socials_mod.merge(known, found))
    pages, added = _site_contacts(conn, fresh, res, fetch)
    flag["contacts_added"] = added
    flag["draft_dropped"] = _drop_no_site_draft(conn, pid)
    store.set_provenance(conn, pid, "site_recheck", flag)
    row2 = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    runner.process_prospect(conn, row2, ctx, {}, None, fetch, res=res, pages=pages)   # site_year, audit, chaîne, score… (contacts déjà faits)
    return res.status, True


def run(conn, search, fetch=None, limit: int = PER_RUN) -> dict:
    stats = {"rechecked": 0, "found": 0}
    if limit <= 0:
        return stats
    from . import fetch as fetch_mod
    fetch = fetch or fetch_mod.Fetcher()
    for row in candidates(conn, limit):
        try:
            _status, saved = recheck(conn, row, search, fetch)
        except SearchUnavailable:
            conn.rollback()
            break                                              # moteurs indisponibles : rien n'est conclu, la suite au prochain passage
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête pas les autres ; marquée pour ne pas boucler
            conn.rollback()
            log.warning("[local] nouvelle recherche de site #%s en échec : %s", row["id"], exc)
            store.set_provenance(conn, row["id"], "site_recheck", {"checked_at": runner._now_iso(), "error": str(exc)[:160]})
            conn.commit()
            continue
        stats["rechecked"] += 1
        if saved:
            stats["found"] += 1
        conn.commit()
    return stats


def _no_search(_query: str) -> list:
    """Passage Google Places : AUCUNE requête vers les moteurs de recherche (seule l'URL de la fiche Google est candidate)."""
    return []


def places_backfill_active(conn) -> bool:
    row = db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey=%s LIMIT 1", (PLACES_SETTING,))
    v = row["value_json"] if row else None
    v = json.loads(v) if isinstance(v, (str, bytes)) else v
    return bool(isinstance(v, dict) and v.get("enabled") is True)


def places_backfill(conn, fetch=None, limit: int = PLACES_PER_RUN) -> dict:
    """Google Places sur les fiches « sans site » DÉJÀ revérifiées avant que la clé existe (bouton de la page Statistiques, réglage `local_places_backfill`).
    Mêmes critères que `candidates`, sans le filtre `site_recheck`, SANS provenance `places` ; brouillon rédigé d'abord. Pour chacune : l'URL de la fiche
    Google, vérifiée sur le site lui-même (aucune requête moteur), puis la même suite que `recheck`. Plus aucune fiche : le réglage s'éteint tout seul.
    Clé refusée ou budget du mois atteint : le passage s'arrête sans erreur (réglage conservé)."""
    stats = {"asked": 0, "found": 0, "done": False}
    from . import places
    if limit <= 0 or not places.enabled() or not places_backfill_active(conn):
        return stats
    rows = candidates(conn, limit, NO_PLACES)
    if not rows:
        db.execute(conn, "UPDATE settings SET value_json=%s WHERE skey=%s", (json.dumps({"enabled": False}), PLACES_SETTING))
        conn.commit()
        stats["done"] = True
        log.info("[local] Google Places sur les fiches déjà trouvées : terminé, réglage désactivé")
        return stats
    from . import fetch as fetch_mod
    fetch = fetch or fetch_mod.Fetcher()
    for row in rows:
        if not places.enabled() or places.month_used(conn) >= places.MONTHLY_BUDGET:
            break                                              # clé refusée ou plafond du mois : la suite au prochain mois / à la prochaine clé
        try:
            ctx = runner.Ctx(conn, _campaign(conn, row["id"]))
            company = runner._company(conn, row)
            urls = runner._places_urls(conn, row)
            if not urls:
                conn.commit()                                  # aucun site chez Google (provenance `places` posée) : rien d'autre à faire
                stats["asked"] += 1
                continue
            res = sitefinder.resolve(company, _no_search, fetch, ctx.site_thresholds, strategy_budget=0,
                                     known_urls=runner._known_urls(conn, row), candidate_urls=urls)
            _status, saved = _conclude(conn, row, ctx, company, res, fetch, only_if_found=True)
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête pas les autres ; `places` est déjà posée : pas de boucle
            conn.rollback()
            log.warning("[local] Google Places sur la fiche #%s en échec : %s", row["id"], exc)
            continue
        stats["asked"] += 1
        stats["found"] += int(saved)
        conn.commit()
    return stats


def main() -> int:
    lock = runner._acquire_lock()
    if lock is None:
        return 0                                               # une campagne tourne : elle a la priorité sur les moteurs
    try:
        with db.connect() as conn:
            stats = run(conn, engines.SearchPool(conn))
        if stats["rechecked"]:
            log.info("[local] nouvelle recherche de sites : %s", json.dumps(stats))
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
