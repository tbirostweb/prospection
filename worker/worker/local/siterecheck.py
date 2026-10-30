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
Sites de fiche Google perdus (`places_site_check`, LOCAL_PLACES_SITE_CHECK_PER_RUN par passage) : URL redemandée par Place Details, vérifiée
sur le site, même suite ; verdict dans la provenance `places.site_checked`.
Fiches de toute origine (campagne, import CSV, veille) : le lien de campagne n'est plus exigé (réglages de la campagne la plus récente, sinon ceux
par défaut). Sans site sûr, une revérification qui couvre de nouvelles stratégies relève la confiance d'absence (`raise_absence`).
`run` utilise les moteurs : appelé sous le verrou des campagnes (`enrich.main`) ; les passages Google Places n'utilisent aucun moteur (verrou de
l'enrichissement seulement). Chaque passage est borné (nombre, `deadline`) et dit pourquoi il s'est arrêté (`stop` : nothing, limit, time, engines,
quota, off).
"""
from __future__ import annotations

import json
import sys
import time

from .. import db
from ..config import env, log
from . import contacts, engines, places, runner, sitefinder, socials as socials_mod, store
from .errors import SearchUnavailable

PER_RUN = int(env("LOCAL_SITE_RECHECK_PER_RUN", "10") or 10)
PLACES_PER_RUN = int(env("LOCAL_PLACES_BACKFILL_PER_RUN", "1") or 1)    # 2 passages/heure × 13 h ≈ 26 fiches/jour, sous le quota Google de 30/jour
PLACES_SITE_PER_RUN = int(env("LOCAL_PLACES_SITE_CHECK_PER_RUN", "5") or 5)   # Place Details par passage : 4 passages/h × 13 h × 5 ≈ 260 au plus/jour (réserve du jour)
PLACES_SETTING = "local_places_backfill"
RECHECK_STRATEGIES = 2                 # recherches au plus par fiche : nom + ville, puis la suivante (les 6 d'origine n'avaient rien donné)
UNTOUCHABLE = "p.contacted_at IS NULL AND (p.auto_state IS NULL OR p.auto_state = 'queued')"


NO_SITE_FILTER = f"""p.website_status IN ('NOT_FOUND','UNCERTAIN') AND {UNTOUCHABLE}
          AND p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND p.pipeline_stage = 'DONE'
          AND (p.category IS NULL OR p.category <> 'IGNORER') AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT','CONTACTED')"""
# critères communs à la revérification et aux passages Google Places ; plus de lien de campagne exigé (fiches importées comprises)
NO_PLACES = "JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.places') IS NULL"
LOST_SITE = """JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.places.had_website')) = 'true'
          AND JSON_TYPE(JSON_EXTRACT(p.provenance, '$.places.place_id')) = 'STRING'
          AND JSON_EXTRACT(p.provenance, '$.places.site_checked_at') IS NULL"""          # site sur la fiche Google, jamais vérifié (URL perdue)


ERROR_RETRY_HOURS = 24
# Pas encore revérifiée, OU revérification en ERREUR (provenance sans `status`) il y a plus de ERROR_RETRY_HOURS : sans cette reprise, la fiche
# restait « revérification pas encore faite » (complete.site_reason) sans plus aucun passage prévu pour elle.
NOT_RECHECKED = f"""(JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.site_recheck') IS NULL
          OR (JSON_EXTRACT(p.provenance, '$.site_recheck.status') IS NULL AND JSON_EXTRACT(p.provenance, '$.site_recheck.error') IS NOT NULL
              AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.site_recheck.checked_at')), '')
                  < DATE_FORMAT(UTC_TIMESTAMP() - INTERVAL {ERROR_RETRY_HOURS} HOUR, '%%Y-%%m-%%dT%%H:%%i:%%sZ')))"""


def candidates(conn, limit: int, where: str = NOT_RECHECKED) -> list[dict]:
    """Fiches « sans site » jamais revérifiées (ou revérification en erreur depuis un jour) et jamais contactées : brouillon déjà rédigé
    d'abord (il serait faux), puis les plus prometteuses. `where` : condition de provenance en plus des critères communs (par défaut : pas
    encore revérifiée ; le passage Google Places pose NO_PLACES)."""
    return db.fetch_all(conn, f"""SELECT p.* FROM local_prospects p
        WHERE {NO_SITE_FILTER} AND {where}
        ORDER BY (p.draft_message IS NOT NULL) DESC, FIELD(p.category, 'A_EXAMINER', 'A_CONTACTER', 'TRES_BON') DESC, p.prospect_score DESC, p.id
        LIMIT %s""", (limit,))


DEFAULT_CAMPAIGN = {"radius_km": 30}                 # fiche sans campagne (import CSV) : réglages généraux, rayon par défaut


def _campaign(conn, pid: int) -> dict | None:
    return db.fetch_one(conn, """SELECT c.* FROM local_campaigns c JOIN local_prospect_campaigns pc ON pc.campaign_id = c.id
                                 WHERE pc.prospect_id = %s ORDER BY c.id DESC LIMIT 1""", (pid,))


def ctx_for(conn, pid: int):
    """Réglages du passage pour ce prospect : ceux de sa campagne la plus récente, sinon les réglages généraux (fiche importée)."""
    return runner.Ctx(conn, _campaign(conn, pid) or DEFAULT_CAMPAIGN)


def _jv(v) -> dict:
    v = json.loads(v) if isinstance(v, (str, bytes)) else v
    return v if isinstance(v, dict) else {}


def raise_absence(conn, row: dict, res) -> float | None:
    """Fiche NOT_FOUND, nouvelle résolution NOT_FOUND : la confiance d'absence est recalculée sur TOUTES les stratégies essayées (les anciennes
    comptent si la recherche d'origine n'était pas dégradée), comme `sitefinder._finish_absence`. Enregistrée seulement si elle monte.
    Renvoie la nouvelle confiance, ou None."""
    if res.status != "NOT_FOUND" or row.get("website_status") != "NOT_FOUND":
        return None
    ev = _jv(row.get("website_evidence"))
    old = set() if ev.get("degraded") else {s for s in ev.get("strategies") or [] if isinstance(s, str)}
    tried = old | set(res.strategies_tried)
    total = max(int(res.strategies_total or 0), int(row.get("website_search_total") or 0))
    if not total:
        return None
    conf = 0.10 + 0.75 * min(1.0, len(tried) / total)
    if res.degraded:
        conf *= 0.6
    conf = round(min(0.85, conf), 2)
    if conf <= float(row.get("website_absence_confidence") or 0):
        return None
    db.execute(conn, f"""UPDATE local_prospects p SET p.website_absence_confidence=%s, p.website_search_tried=%s, p.website_search_total=%s,
                            p.website_evidence=JSON_SET(COALESCE(p.website_evidence, JSON_OBJECT()), '$.strategies', CAST(%s AS JSON), '$.degraded', %s)
                         WHERE p.id=%s AND p.website_status='NOT_FOUND' AND {UNTOUCHABLE}""",
               (conf, len(tried), total, json.dumps(sorted(tried)), bool(res.degraded), row["id"]))
    return conf


def _site_contacts(conn, row: dict, res, fetch, deep: bool = False) -> tuple[list, list[str]]:
    """Contacts lus sur le site trouvé, AJOUTÉS à ceux déjà connus (jamais remplacés). Renvoie (pages lues, champs ajoutés).
    `deep` : relecture d'un site sans e-mail (pages /contact et /mentions-legales cherchées même sans lien)."""
    pid = row["id"]
    pages = contacts.discover(res.pages or [], fetch, res.canonical_domain or "", deep=deep)
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


# Verdicts de `sitefinder.check_site` → verdict sur le site de la fiche Google (provenance `places.site_checked`, page Statistiques)
PLACES_VERDICT = {"other_company": "rejected:other_company", "group_site": "rejected:group_site", "maintenance": "rejected:maintenance",
                  "ssl": "rejected:maintenance", "parked": "rejected:parked", "no_proof": "rejected:no_match"}
SAME_COMPANY = ("maintenance", "ssl", "parked")        # site lié à l'entreprise mais inexploitable : ses coordonnées restent les siennes


def _host(url: str | None) -> str:
    from urllib.parse import urlparse
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def demote_site(conn, row: dict, verdict: str, label: str, a, ctx) -> dict | None:
    """Site retenu (CONFIRMED / PROBABLE) qui ne passe plus la règle d'identité (`sitefinder.check_site`) : la fiche redevient « site à trancher »
    — NOT_FOUND (à chercher de nouveau : `enrich.search_more`) si c'est le site d'une AUTRE entreprise (domaine ajouté aux mauvais sites de ce
    SIRET), sinon UNCERTAIN avec le site en candidat (groupe, maintenance, certificat invalide, preuve absente : à toi de trancher). Brouillon
    effacé, fiche sortie de la file d'envoi (auto_state queued → NULL), sans alerte ; e-mail / téléphone / page de contact lus sur CE site
    retirés (ceux d'une autre entreprise ou d'un groupe ; l'e-mail d'un site refusé faute de preuve reste, en UNCERTAIN). Jamais une fiche contactée, en séquence, ou dont tu as confirmé le site.
    Renvoie ce qui a changé, ou None si la fiche a bougé entre-temps."""
    pid = row["id"]
    status = "NOT_FOUND" if verdict == "other_company" else "UNCERTAIN"
    domain = getattr(a, "domain", None) or row.get("canonical_domain") or _host(row.get("website_url"))
    conf = round(ctx.site_thresholds.uncertain if status == "UNCERTAIN" else min(float(getattr(a, "confidence", 0) or 0), 0.30), 2)
    old = _jv(row.get("website_evidence"))
    note = {"code": "identity_rejected", "label": f"⚠ site {row.get('website_url')} retiré par la vérification d'identité : {label}", "weight": 0.0}
    cand = {"url": getattr(a, "url", None) or row.get("website_url"), "domain": domain, "confidence": conf, "reachable": bool(getattr(a, "reachable", False)),
            "evidence": list(getattr(a, "evidence", None) or []), "verdict": verdict}
    keep = status == "UNCERTAIN"                     # NOT_FOUND : recherche à refaire avec la nouvelle règle (stratégies remises à zéro)
    ev = {"evidence": [note] + list(getattr(a, "evidence", None) or []), "candidates": [cand], "queries": (old.get("queries") or []) if keep else [],
          "strategies": (old.get("strategies") or []) if keep else [], "socials": old.get("socials") or [], "degraded": bool(old.get("degraded")),
          "identity": {"verdict": verdict, "label": label, "previous_status": row.get("website_status"), "previous_url": row.get("website_url")}}
    with conn.cursor() as cur:
        n = cur.execute(f"""UPDATE local_prospects p SET p.website_status=%s, p.website_url=NULL, p.website_confidence=%s, p.website_evidence=%s,
                                p.website_final_url=NULL, p.canonical_domain=NULL, p.redirect_chain=NULL, p.website_absence_confidence=NULL,
                                p.website_search_tried=%s, p.website_checked_at=UTC_TIMESTAMP()
                            WHERE p.id=%s AND {UNTOUCHABLE} AND p.website_status IN ('CONFIRMED','PROBABLE') AND COALESCE(p.site_review, '') <> 'CONFIRMED'""",
                        (status, conf, json.dumps(ev, ensure_ascii=False, default=str), len(ev["strategies"]), pid))
    if not n:
        return None
    if verdict == "other_company" and domain:
        db.execute(conn, "INSERT IGNORE INTO local_bad_sites (siret, fingerprint, domain, reason) VALUES (%s,%s,%s,%s)",
                   (row.get("siret"), row.get("fingerprint"), domain[:255], f"autre entreprise (vérification d'identité) : {label}"[:255]))
    cleared: list[str] = []
    if verdict not in SAME_COMPANY and domain:
        prov = _jv(row.get("provenance"))
        sets = []
        for kind, cols in (("email", "email=NULL, email_kind=NULL"), ("phone", "phone=NULL, phone_confidence=NULL")):
            src = prov.get(kind) if isinstance(prov.get(kind), dict) else {}
            if row.get(kind) and _host(src.get("url")) == domain:
                if kind == "email" and verdict == "no_proof":      # preuve absente (≠ autre entreprise / groupe) : l'e-mail reste, en « incertain »
                    sets.append("email_kind='UNCERTAIN'")
                    store.set_provenance(conn, pid, "email", {**src, "site_unverified": True, "note": "site non vérifié (preuve d'identité absente)"})
                    continue
                sets.append(cols)
                cleared.append(kind)
        if _host(row.get("contact_page")) == domain:
            sets.append("contact_page=NULL, contact_form=0")
            cleared.append("contact_page")
        if _host(row.get("contact_source_url")) == domain:
            sets.append("contact_source_url=NULL")
        if sets:
            db.execute(conn, f"UPDATE local_prospects SET {', '.join(sets)} WHERE id=%s", (pid,))
    places_flag = (_jv(row.get("provenance")).get("places") or {})
    if isinstance(places_flag, dict) and places_flag.get("site_checked") in ("confirmed", "probable"):
        db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.places.site_checked', %s) WHERE id=%s",
                   (PLACES_VERDICT.get(verdict, "rejected:no_match"), pid))
    dropped = _drop_no_site_draft(conn, pid)
    fresh = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    if fresh is not None:
        runner._rescore(conn, fresh, ctx)                  # score recalculé sans ce site
    log.info("[local] fiche #%s : site %s retiré (%s), fiche %s", pid, row.get("website_url"), verdict, status)
    return {"status": status, "draft_dropped": dropped, "contacts_cleared": cleared}


def recheck(conn, row: dict, search, fetch) -> tuple[str, bool]:
    """UNE nouvelle résolution pour ce prospect. Renvoie (niveau trouvé, site enregistré ?). SearchUnavailable remonte (rien n'est écrit)."""
    ctx = ctx_for(conn, row["id"])
    company = runner._company(conn, row)
    suggested = runner._places_urls(conn, row)
    res = sitefinder.resolve(company, search, fetch, ctx.site_thresholds, strategy_budget=RECHECK_STRATEGIES,
                             known_urls=runner._known_urls(conn, row), dns=ctx.dns, candidate_urls=suggested)
    runner.record_places_check(conn, row["id"], res, suggested)
    return _conclude(conn, row, ctx, company, res, fetch)


def _conclude(conn, row: dict, ctx, company: dict, res, fetch, only_if_found: bool = False) -> tuple[str, bool]:
    """Suite d'une résolution (revérification ou passage Google Places) : site accepté = enregistré + contacts ajoutés + traitement du runner +
    brouillon « sans site » effacé. `only_if_found` : sans site sûr, aucune provenance `site_recheck` n'est posée (la fiche n'a pas été revérifiée)."""
    pid = row["id"]
    flag = {"checked_at": runner._now_iso(), "previous": row.get("website_status"), "status": res.status, "confidence": round(res.confidence, 2),
            "url": res.url if res.accepted else None}
    fresh = db.fetch_one(conn, f"SELECT p.* FROM local_prospects p WHERE p.id=%s AND {UNTOUCHABLE}", (pid,))
    if not res.accepted or fresh is None:                             # rien de sûr (ou contacté entre-temps) : l'ancienne résolution reste
        if fresh is not None and (conf := raise_absence(conn, fresh, res)) is not None:
            flag["absence_confidence"] = conf                          # nouvelles stratégies sans résultat : l'absence de site est plus sûre
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


def email_domain_site(conn, row: dict, ctx, fetch, res=None) -> tuple[str, bool]:
    """Site du domaine de l'e-mail pro de la fiche (`sitefinder.resolve_email_domain`, sans moteur), pour une fiche « sans site » jamais contactée.
    Renvoie (statut, site retenu ?). `res` : résolution déjà faite (l'envoi la fait dans un fil borné). Provenance `email_domain_site`
    {checked_at, domain, status, url} : une fois par adresse (voir `enrich.email_domain_sites`).
      * CONFIRMED / PROBABLE : enregistré comme une revérification (`_conclude` : contacts ajoutés, suite du runner, brouillon effacé,
        sortie de la file) ; provenance `website` = source « email_domain », preuve « e-mail de la fiche sur ce domaine » dans les preuves ;
      * UNCERTAIN (le site répond, preuve insuffisante) : la fiche passe de « sans site » à « site à trancher » avec le site en candidat, le brouillon
        « sans site » est effacé (le message de création serait faux) ;
      * rien (injoignable, parqué, en construction, autre entreprise) : seule la provenance est posée, l'absence de site reste vraie."""
    pid = row["id"]
    company = runner._company(conn, row)
    dom = sitefinder.email_proof_domain(company)
    if not dom:
        return row.get("website_status") or "NOT_FOUND", False
    if res is None:
        res = sitefinder.resolve_email_domain(company, fetch, ctx.site_thresholds)
    flag = {"checked_at": runner._now_iso(), "domain": dom, "status": res.status}
    saved = False
    fresh = db.fetch_one(conn, f"SELECT p.* FROM local_prospects p WHERE p.id=%s AND {UNTOUCHABLE}", (pid,))
    if fresh is not None and res.accepted:
        saved = _conclude(conn, fresh, ctx, company, res, fetch, only_if_found=True)[1]
        if saved:
            flag["url"] = res.url
            store.set_provenance(conn, pid, "website", {"source": "email_domain", "strategies": [], "status": res.status, "url": res.url,
                                                        "at": runner._now_iso()})
            log.info("[local] fiche #%s : site %s retenu d'après le domaine de son e-mail (%s)", pid, res.url, res.status)
    elif fresh is not None and res.status == "UNCERTAIN" and fresh.get("website_status") in (None, "NOT_FOUND"):
        cand = res.candidates[0]
        flag["url"] = cand["url"]
        with conn.cursor() as cur:
            n = cur.execute(f"""UPDATE local_prospects p SET p.website_status='UNCERTAIN', p.website_confidence=%s, p.website_absence_confidence=NULL,
                                    p.website_evidence=JSON_SET(COALESCE(p.website_evidence, JSON_OBJECT()), '$.evidence', CAST(%s AS JSON),
                                                                '$.candidates', CAST(%s AS JSON)),
                                    p.website_checked_at=UTC_TIMESTAMP()
                                 WHERE p.id=%s AND {UNTOUCHABLE} AND p.website_status <=> %s""",
                            (round(res.confidence, 2), json.dumps(res.evidence, ensure_ascii=False, default=str),
                             json.dumps(res.candidates, ensure_ascii=False, default=str), pid, fresh.get("website_status")))
        if n:
            if "sans_site" in (fresh.get("draft_kind") or ""):
                _drop_no_site_draft(conn, pid)                     # « je n'ai pas trouvé de site » serait faux : brouillon effacé
            runner._rescore(conn, db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,)), ctx)
            log.info("[local] fiche #%s : site %s possible d'après le domaine de son e-mail, à trancher", pid, cand["url"])
    store.set_provenance(conn, pid, "email_domain_site", flag)
    return res.status, saved


def stop_after(rows: list, limit: int) -> str:
    """Raison d'arrêt d'un passage qui a traité toutes ses fiches : « limit » s'il en reste sans doute, « nothing » sinon."""
    return "limit" if len(rows) >= limit else "nothing"


def run(conn, search, fetch=None, limit: int = PER_RUN, deadline: float | None = None) -> dict:
    stats = {"rechecked": 0, "found": 0, "stop": "off"}
    if limit <= 0:
        return stats
    from . import fetch as fetch_mod
    fetch = fetch or fetch_mod.Fetcher()
    rows = candidates(conn, limit)
    stats["stop"] = stop_after(rows, limit)
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            stats["stop"] = "time"
            break
        try:
            _status, saved = recheck(conn, row, search, fetch)
        except SearchUnavailable:
            conn.rollback()
            stats["stop"] = "engines"
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


def _verify_places_urls(conn, row: dict, ctx, company: dict, urls: list[str], fetch) -> bool:
    """URL(s) de la fiche Google VÉRIFIÉES sur le site lui-même, sans aucune requête moteur ; site sûr = même suite que `recheck`. Renvoie : enregistré ?"""
    res = sitefinder.resolve(company, _no_search, fetch, ctx.site_thresholds, strategy_budget=0,
                             known_urls=runner._known_urls(conn, row), candidate_urls=urls)
    runner.record_places_check(conn, row["id"], res, urls)
    return _conclude(conn, row, ctx, company, res, fetch, only_if_found=True)[1]


def verify_places_site(conn, row: dict, url: str, fetch) -> bool:
    """Fiche « sans site » retrouvée par la découverte Google Places (`placesdiscovery`) avec un site sur sa fiche Google : vérifié tout de suite
    (l'URL n'est jamais stockée). Mêmes garde-fous que le passage Google Places : jamais une fiche contactée, écartée, chaîne ou « ne plus contacter »."""
    fresh = db.fetch_one(conn, f"SELECT p.* FROM local_prospects p WHERE p.id=%s AND {NO_SITE_FILTER}", (row["id"],))
    if fresh is None or not url:
        return False
    return _verify_places_urls(conn, fresh, ctx_for(conn, fresh["id"]), runner._company(conn, fresh), [url], fetch)


def places_backfill_active(conn) -> bool:
    row = db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey=%s LIMIT 1", (PLACES_SETTING,))
    v = row["value_json"] if row else None
    v = json.loads(v) if isinstance(v, (str, bytes)) else v
    return bool(isinstance(v, dict) and v.get("enabled") is True)


def places_backfill(conn, fetch=None, limit: int = PLACES_PER_RUN, deadline: float | None = None) -> dict:
    """Google Places sur les fiches « sans site » DÉJÀ revérifiées avant que la clé existe (bouton de la page Statistiques, réglage `local_places_backfill`).
    Mêmes critères que `candidates`, sans le filtre `site_recheck`, SANS provenance `places` ; brouillon rédigé d'abord. Pour chacune : l'URL de la fiche
    Google, vérifiée sur le site lui-même (aucune requête moteur), puis la même suite que `recheck`. Plus aucune fiche : le réglage s'éteint tout seul.
    Clé refusée ou budget du mois atteint : le passage s'arrête sans erreur (réglage conservé)."""
    stats = {"asked": 0, "found": 0, "done": False, "stop": "off"}
    from . import places
    if limit <= 0 or not places.enabled() or not places_backfill_active(conn):
        return stats
    rows = candidates(conn, limit, NO_PLACES)
    stats["stop"] = stop_after(rows, limit)
    if not rows:
        db.execute(conn, "UPDATE settings SET value_json=%s WHERE skey=%s", (json.dumps({"enabled": False}), PLACES_SETTING))
        conn.commit()
        stats["done"] = True
        log.info("[local] Google Places sur les fiches déjà trouvées : terminé, réglage désactivé")
        return stats
    from . import fetch as fetch_mod
    fetch = fetch or fetch_mod.Fetcher()
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            stats["stop"] = "time"
            break
        if not places.enabled() or places.month_used(conn) >= places.MONTHLY_BUDGET:
            stats["stop"] = "quota"
            break                                              # clé refusée ou plafond du mois : la suite au prochain mois / à la prochaine clé
        try:
            ctx = ctx_for(conn, row["id"])
            company = runner._company(conn, row)
            places.stop_reason = None
            urls = runner._places_urls(conn, row)
            if not urls and (places.stop_reason or not places.enabled()):
                conn.commit()
                stats["stop"] = "quota"
                break                                          # clé refusée ou quota pendant l'appel : rien de conclu pour cette fiche
            if not urls:
                conn.commit()                                  # aucun site chez Google (provenance `places` posée) : rien d'autre à faire
                stats["asked"] += 1
                continue
            saved = _verify_places_urls(conn, row, ctx, company, urls, fetch)
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête pas les autres ; `places` est déjà posée : pas de boucle
            conn.rollback()
            log.warning("[local] Google Places sur la fiche #%s en échec : %s", row["id"], exc)
            continue
        stats["asked"] += 1
        stats["found"] += int(saved)
        conn.commit()
    return stats


def places_site_check(conn, fetch=None, limit: int = PLACES_SITE_PER_RUN, deadline: float | None = None) -> dict:
    """Sites de fiche Google PERDUS : fiche « sans site » dont la fiche Google avait un site (`had_website`), place_id connu, jamais vérifié
    (`site_checked_at` absent) — l'URL n'était qu'en mémoire et le passage s'est arrêté avant (plafond, délai, erreur), ou elle date d'avant le
    barème du site déclaré. Pour chacune (au plus `limit`, LOCAL_PLACES_SITE_CHECK_PER_RUN) : l'URL redemandée par Place Details (une requête du
    budget mensuel), vérifiée sur le site lui-même sans aucune requête moteur, puis la même suite que `recheck` (contacts ajoutés, score, brouillon
    « sans site » effacé). Le verdict est posé (`places.site_checked`) : jamais redemandée. Clé refusée, quota ou budget : arrêt sans erreur."""
    stats = {"asked": 0, "found": 0, "rejected": 0, "stop": "off"}
    from . import places
    if limit <= 0 or not places.enabled():
        if places.API_KEY and limit > 0:
            stats["stop"] = "quota"                            # clé présente mais coupée pour ce processus (quota, clé refusée)
        return stats
    rows = candidates(conn, limit, LOST_SITE)
    stats["stop"] = stop_after(rows, limit)
    if not rows:
        return stats
    from . import fetch as fetch_mod
    fetch = fetch or fetch_mod.Fetcher()
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            stats["stop"] = "time"
            break
        try:
            company = runner._company(conn, row)
            url, concluded = places.recover_url(conn, row, company)
            if not concluded:
                conn.rollback()
                stats["stop"] = "quota"
                break                                          # clé refusée, quota, budget du mois ou Google injoignable : au prochain passage
            stats["asked"] += 1
            if not url:
                stats["rejected"] += 1                         # verdict déjà posé (« no_url », « rejected:social »…)
                conn.commit()
                continue
            saved = _verify_places_urls(conn, row, ctx_for(conn, row["id"]), company, [url], fetch)
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête pas les autres ; marquée pour ne pas boucler
            conn.rollback()
            log.warning("[local] site de la fiche Google #%s : vérification en échec : %s", row["id"], exc)
            places.record_site_check(conn, row["id"], f"error:{exc.__class__.__name__}")
            conn.commit()
            continue
        stats["found" if saved else "rejected"] += 1
        conn.commit()
    return stats


def main() -> int:
    lock = runner._acquire_lock()
    if lock is None:
        return 0                                               # une campagne tourne : elle a la priorité sur les moteurs
    try:
        with db.connect() as conn:
            places.PURPOSE = "enrich"
            stats = run(conn, engines.SearchPool(conn))
        if stats["rechecked"]:
            log.info("[local] nouvelle recherche de sites : %s", json.dumps(stats))
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
