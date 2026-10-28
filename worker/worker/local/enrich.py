"""Enrichissement des fiches EXISTANTES (qualité de la liste, pas de nouvelles découvertes) :  python -m worker.local.emailsearch [--no-engines]

Chaque « raison » pour laquelle une fiche n'est pas complète (complete.site_reason / complete.contact_reason) a ici un passage qui la traite, borné
(nombre par passage, budget de temps), idempotent (provenance posée : jamais deux fois la même chose, aucune boucle) :

  sans moteur (verrou de l'enrichissement seulement : tourne PENDANT une campagne, sur des fiches qui ne sont pas dans une campagne en cours)
    company_state   état officiel (SIRENE, BODACC) : fermées, cessées, radiées ou en procédure collective écartées (companystate.run ; jamais
                    vérifiées d'abord, puis tous les 30 jours, 7 jours pour les fiches en file d'envoi ou en relances)
    naf             métier d'après le code NAF (naflabels.backfill)
    manual          e-mails ajoutés à la main : score recalculé
    stuck           fiches bloquées « traitement en cours » hors campagne en cours (site connu) : traitement fini (runner.process_prospect)
    site_identity   sites déjà retenus (CONFIRMED / PROBABLE) relus avec la règle d'identité (sitefinder.check_site : l'entreprise doit figurer
                    sur le site ; autre entreprise, site de groupe, maintenance, certificat invalide écartés) — fiches en file d'envoi d'abord,
                    une fois par version de la règle ; site qui ne passe plus : « à trancher », brouillon effacé, sortie de la file
    places_site     site de la fiche Google jamais vérifié : Place Details, vérifié sur le site (siterecheck.places_site_check)
    places_backfill fiches jamais interrogées sur Google Places (bouton de la page Statistiques)
    site_guess      devinettes de domaine + domaine de l'e-mail, sans moteur, pour toute fiche sans site sûr (une fois ; à nouveau après un doute
                    de la vérification d'avant l'envoi : doute levé si rien ne ressort)
    unreachable     site injoignable : nouvel essai après UNREACHABLE_RETRY_DAYS jours, complete.UNREACHABLE_MAX_TRIES fois ; ensuite « en panne »
    site_email      site vérifié sans e-mail : pages contact / mentions légales relues
  avec moteurs (verrou des campagnes : le rythme par moteur est tenu en mémoire du processus, deux chercheurs en même temps le doubleraient)
    recheck         revérification des fiches sans site / incertaines (siterecheck.run)
    search_more     recherche trop maigre (confiance d'absence < ABSENCE_MIN) ou jamais faite : stratégies pas encore essayées (3 essais à 1 jour
                    d'écart, puis tous les 14 jours : jamais oubliée)
    stuck_search    fiches bloquées dont le site n'a jamais été cherché
    email           recherche d'e-mail (emailsearch.run)

Ordre : si le verrou des campagnes est libre, la partie moteurs d'abord (au plus ENGINE_BUDGET_S), puis la partie sans moteur ; sinon la partie
sans moteur, puis attente bornée du verrou (LOCK_WAIT_S). Chaque sous-passage a STEP_BUDGET_S au plus, le passage entier TIME_BUDGET_S.
`--no-engines` : partie sans moteur seulement (passages intermédiaires). Google Places : accès aux réserves du jour et du mois (`places.PURPOSE`).
Sans journaux sur le VPS : un état par passage dans `local_enrich_runs` (migration 033), affiché par la page Statistiques — heure, durée, ce que
chaque sous-passage a traité et pourquoi il s'est arrêté (rien à faire, plafond, budget de temps, quota, moteurs indisponibles, verrou pris).
"""
from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace

from .. import db
from ..config import log
from . import engines, errors, places, runner, sitefinder, siterecheck, store
from .errors import SearchUnavailable
from .signals import ABSENCE_MIN


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


TIME_BUDGET_S = _env_int("LOCAL_ENRICH_TIME_BUDGET_S", 1200)        # passage entier (attente du verrou comprise), sous l'écart entre deux passages
STEP_BUDGET_S = _env_int("LOCAL_ENRICH_STEP_BUDGET_S", 180)         # un sous-passage : aucun ne prend tout le temps des autres
ENGINE_BUDGET_S = _env_int("LOCAL_ENRICH_ENGINE_BUDGET_S", 300)     # temps passé sous le verrou des campagnes (elles attendent au plus 6 min)
LOCK_WAIT_S = _env_int("LOCAL_ENRICH_LOCK_WAIT_S", 600)             # attente du verrou des campagnes, après la partie sans moteur
SITE_GUESS_PER_RUN = _env_int("LOCAL_SITE_GUESS_PER_RUN", 20)
SITE_SEARCH_PER_RUN = _env_int("LOCAL_SITE_SEARCH_PER_RUN", 3)      # ≈ 6 requêtes chacune
UNREACHABLE_PER_RUN = _env_int("LOCAL_UNREACHABLE_RETRY_PER_RUN", 5)
SITE_EMAIL_PER_RUN = _env_int("LOCAL_SITE_EMAIL_PER_RUN", 10)
SITE_EMAIL_RETRY_DAYS = _env_int("LOCAL_SITE_EMAIL_RETRY_DAYS", 14)  # site retenu toujours sans e-mail : relu après ce délai…
SITE_EMAIL_MAX_TRIES = _env_int("LOCAL_SITE_EMAIL_MAX_TRIES", 3)     # … au plus ce nombre de lectures en tout (la première comprise)
STUCK_PER_RUN = _env_int("LOCAL_STUCK_PER_RUN", 10)
SITE_IDENTITY_PER_RUN = _env_int("LOCAL_SITE_IDENTITY_PER_RUN", 25)  # ≈ 3 lectures chacune, aucune requête aux moteurs
IDENTITY_RETRY_DAYS, IDENTITY_MAX_TRIES = 1, 3                       # site injoignable à la relecture : repris le lendemain, 3 fois au plus
UNREACHABLE_RETRY_DAYS = 3
# Recherche complétée : les SEARCH_MORE_TRIES premiers essais à SEARCH_MORE_DAYS jour(s) d'écart (moteurs dégradés : souvent passager), ensuite
# tous les SEARCH_MORE_LATER_DAYS jours, sans limite : une fiche « recherche trop maigre » a toujours un passage prévu (jamais oubliée).
SEARCH_MORE_TRIES, SEARCH_MORE_DAYS, SEARCH_MORE_LATER_DAYS = 3, 1, 14
KEEP_RUNS = 500

FREE_STEPS = ("company_state", "naf", "manual", "stuck", "site_identity", "places_site", "places_backfill", "site_guess", "unreachable", "site_email")
ENGINE_STEPS = ("recheck", "search_more", "stuck_search", "email")
STOP_TEXT = {"time": "budget de temps", "quota": "quota", "engines": "moteurs indisponibles", "error": "erreur", "lock": "verrou pris"}

BASE = f"""p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND (p.category IS NULL OR p.category <> 'IGNORER')
          AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT','CONTACTED') AND {siterecheck.UNTOUCHABLE}"""
RUNNING = """EXISTS (SELECT 1 FROM local_prospect_campaigns pc JOIN local_campaigns c ON c.id = pc.campaign_id
                    WHERE pc.prospect_id = p.id AND c.enabled = 1 AND c.status IN ('queued','running'))"""
ORDER = "(p.draft_message IS NOT NULL) DESC, FIELD(p.category, 'A_EXAMINER', 'A_CONTACTER', 'TRES_BON') DESC, p.prospect_score DESC, p.id"


def _prov(path: str) -> str:
    return f"JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.{path}')"


def _date(path: str) -> str:
    return f"STR_TO_DATE(JSON_UNQUOTE({_prov(path)}), '%%Y-%%m-%%dT%%H:%%i:%%sZ')"


def _mark(conn, key: str, tries: bool = False, **fixed):
    """Provenance posée sur une fiche en échec (`key` = {checked_at, error}) : le passage ne la reprend plus (ou plus tard, essai compté)."""
    def mark(row: dict, exc: Exception) -> None:
        flag = {"checked_at": runner._now_iso(), "error": type(exc).__name__[:60], **fixed}
        if tries:
            prev = siterecheck._jv(row.get("provenance")).get(key)
            flag["tries"] = int((prev or {}).get("tries") or 0) + 1 if isinstance(prev, dict) else 1
        store.set_provenance(conn, row["id"], key, flag)
    return mark


def _rows(conn, where: str, limit: int, order: str = ORDER) -> list[dict]:
    return db.fetch_all(conn, f"SELECT p.* FROM local_prospects p WHERE {where} ORDER BY {order} LIMIT %s", (limit,))


def _out(done: int = 0, found: int = 0, stop: str = "nothing", detail: str | None = None) -> dict:
    return {"done": done, "found": found, "stop": stop, "detail": detail}


def _loop(conn, rows: list[dict], limit: int, deadline: float | None, one, mark=None) -> dict:
    """Traite `rows` une à une (`one(row) -> trouvé ?`) jusqu'au `deadline`. SearchUnavailable arrête le passage (rien de conclu pour la fiche
    en cours). Une fiche en échec n'arrête pas les autres : `mark(row, exc)` pose sa provenance (jamais reprise en boucle)."""
    out = _out(stop=siterecheck.stop_after(rows, limit))
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            out["stop"] = "time"
            break
        try:
            found = one(row)
        except SearchUnavailable as exc:
            conn.rollback()
            out.update(stop="engines", detail=str(exc)[:120])
            break
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            log.warning("[local] enrichissement de la fiche #%s en échec : %s", row["id"], exc)
            if mark is not None:
                mark(row, exc)
            conn.commit()
            out["errors"] = out.get("errors", 0) + 1
            continue
        out["done"] += 1
        out["found"] += int(bool(found))
        conn.commit()
    return out


# ── Sans moteur ─────────────────────────────────────────────────────────────────────────────────────
def _guess(conn, row: dict, fetch) -> tuple[object, bool]:
    """Résolution SANS moteur : domaine de l'adresse e-mail, domaines devinés d'après le nom (DNS puis lecture), sites déjà connus ; site sûr =
    même suite que la revérification (contacts ajoutés, score, brouillon « sans site » effacé)."""
    ctx, company = siterecheck.ctx_for(conn, row["id"]), runner._company(conn, row)
    urls = [u] if (u := places._session.get(row["id"])) else []
    res = sitefinder.resolve(company, siterecheck._no_search, fetch, ctx.site_thresholds, strategy_budget=0, known_urls=runner._known_urls(conn, row),
                             dns=ctx.dns, candidate_urls=urls)
    return res, siterecheck._conclude(conn, row, ctx, company, res, fetch, only_if_found=True)[1]


def site_guess(conn, fetch, limit: int = SITE_GUESS_PER_RUN, deadline: float | None = None) -> dict:
    if limit <= 0:
        return _out(stop="off")
    doubt = f"COALESCE(JSON_UNQUOTE({_prov('presend_site.doubt')}), '') = 'true'"
    old_version = f"COALESCE(CAST(JSON_UNQUOTE({_prov('site_guess.version')}) AS UNSIGNED), 1) < {sitefinder.GUESS_VERSION}"
    rows = _rows(conn, f"""p.website_status IN ('NOT_FOUND','UNCERTAIN') AND p.pipeline_stage = 'DONE' AND {BASE}
        AND ({_prov('site_guess')} IS NULL OR {old_version}
             OR ({doubt} AND COALESCE(JSON_UNQUOTE({_prov('site_guess.checked_at')}), '') < COALESCE(JSON_UNQUOTE({_prov('presend_site.at')}), '')))""",
                 limit, f"{doubt} DESC, ({_prov('site_guess')} IS NULL) DESC, {ORDER}")

    def one(row):
        from . import mailer
        res, saved = _guess(conn, row, fetch)
        best = next((c for c in res.candidates if c.get("reachable") and not c.get("parked") and c.get("confidence", 0) >= sitefinder.UNCERTAIN_MIN), None)
        store.set_provenance(conn, row["id"], "site_guess", {"checked_at": runner._now_iso(), "status": res.status, "found": saved,
                                                             "version": sitefinder.GUESS_VERSION, "candidate": (best or {}).get("domain")})
        pre = siterecheck._jv(row.get("provenance")).get("presend_site")
        if not saved and isinstance(pre, dict) and pre.get("doubt"):
            dom = mailer.email_domain(row.get("email"))
            if res.status != "UNCERTAIN" and not any(c.get("domain") == dom and c.get("reachable") and not c.get("parked") for c in res.candidates if dom):
                store.set_provenance(conn, row["id"], "presend_site", {"at": runner._now_iso(), "result": "none", "tries": 0, "cleared_by": "site_guess"})
        return saved
    return _loop(conn, rows, limit, deadline, one, _mark(conn, "site_guess"))


def site_identity(conn, fetch, limit: int = SITE_IDENTITY_PER_RUN, deadline: float | None = None) -> dict:
    """Rattrapage : chaque site retenu (CONFIRMED / PROBABLE, hors site que tu as confirmé) est relu SANS moteur (accueil, mentions légales,
    contact) avec la règle d'identité de cette version (`sitefinder.IDENTITY_VERSION`) : fiches en file d'envoi d'abord, puis celles qui ont un
    brouillon, puis les autres ; les fiches déjà contactées en dernier, et pour elles la provenance seule (rien d'autre n'est touché). Site qui
    ne passe plus : `siterecheck.demote_site` (« à trancher », brouillon effacé, sortie de la file, sans alerte). Injoignable : repris le
    lendemain, IDENTITY_MAX_TRIES fois au plus (l'envoi, lui, revérifie juste avant : `outbox.presend_identity`). Provenance `site_identity`."""
    if limit <= 0:
        return _out(stop="off")
    ver = sitefinder.IDENTITY_VERSION
    tries = f"COALESCE(CAST(JSON_UNQUOTE({_prov('site_identity.tries')}) AS UNSIGNED), 0)"
    rows = _rows(conn, f"""p.website_status IN ('CONFIRMED','PROBABLE') AND p.website_url IS NOT NULL AND COALESCE(p.site_review, '') <> 'CONFIRMED'
        AND NOT {RUNNING}
        AND (COALESCE(CAST(JSON_UNQUOTE({_prov('site_identity.version')}) AS UNSIGNED), 0) < {ver}
             OR (JSON_UNQUOTE({_prov('site_identity.result')}) = 'unreachable' AND {tries} < {IDENTITY_MAX_TRIES}
                 AND {_date('site_identity.checked_at')} < UTC_TIMESTAMP() - INTERVAL {IDENTITY_RETRY_DAYS} DAY))""",
                 limit, f"({siterecheck.UNTOUCHABLE}) DESC, (p.auto_state <=> 'queued') DESC, {ORDER}")

    def one(row):
        pid = row["id"]
        ctx, company = siterecheck.ctx_for(conn, pid), runner._company(conn, row)
        verdict, label, a = sitefinder.check_site(company, row["website_url"], fetch, ctx.site_thresholds)
        prev = siterecheck._jv(row.get("provenance")).get("site_identity")
        flag = {"checked_at": runner._now_iso(), "version": ver, "result": verdict, "url": (row.get("website_url") or "")[:200]}
        if verdict != "ok":
            flag["label"] = label[:200]
        if verdict == "unreachable":
            flag["tries"] = (int((prev or {}).get("tries") or 0) if isinstance(prev, dict) else 0) + 1
        demoted = None
        if verdict not in ("ok", "unreachable"):
            if row.get("contacted_at") or row.get("auto_state") not in (None, "queued"):
                flag["contacted"] = True                       # déjà contactée : rien n'est changé, seulement noté
                log.warning("[local] fiche contactée #%s : son site %s ne passe plus la vérification d'identité (%s)", pid, row.get("website_url"), verdict)
            else:
                demoted = siterecheck.demote_site(conn, row, verdict, label, a, ctx)
                if demoted:
                    flag.update(demoted)
        store.set_provenance(conn, pid, "site_identity", flag)
        return bool(demoted)
    return _loop(conn, rows, limit, deadline, one, _mark(conn, "site_identity", version=sitefinder.IDENTITY_VERSION, result="error"))


def unreachable_retry(conn, fetch, limit: int = UNREACHABLE_PER_RUN, deadline: float | None = None) -> dict:
    """Site injoignable : rouvert après UNREACHABLE_RETRY_DAYS jours. Il répond et il est le sien : enregistré (même suite que la revérification) ;
    toujours injoignable : essai compté (au bout de complete.UNREACHABLE_MAX_TRIES, « site en panne » : tranché) ; il répond mais n'est plus le
    sien (domaine parqué, autre entreprise) : la fiche redevient « sans site », à chercher (search_more)."""
    from . import complete
    if limit <= 0:
        return _out(stop="off")
    rows = _rows(conn, f"""p.website_status = 'UNREACHABLE' AND p.website_url IS NOT NULL AND p.pipeline_stage = 'DONE' AND {BASE}
        AND COALESCE(CAST(JSON_UNQUOTE({_prov('unreachable_retry.tries')}) AS UNSIGNED), 0) < {complete.UNREACHABLE_MAX_TRIES}
        AND COALESCE({_date('unreachable_retry.checked_at')}, p.website_checked_at, p.discovered_at, '2000-01-01')
            < UTC_TIMESTAMP() - INTERVAL {UNREACHABLE_RETRY_DAYS} DAY""", limit)

    def one(row):
        pid = row["id"]
        prev = siterecheck._jv(row.get("provenance")).get("unreachable_retry")
        tries = int((prev or {}).get("tries") or 0) + 1 if isinstance(prev, dict) else 1
        got = fetch(row["website_url"])
        saved, status = False, "UNREACHABLE"
        if got is not None and getattr(got, "ok", False) and getattr(got, "text", ""):
            ctx, company = siterecheck.ctx_for(conn, pid), runner._company(conn, row)
            res = sitefinder.resolve(company, siterecheck._no_search, fetch, ctx.site_thresholds, strategy_budget=0,
                                     known_urls=runner._known_urls(conn, row), candidate_urls=[row["website_url"]])
            status = res.status
            if res.accepted:
                saved = siterecheck._conclude(conn, row, ctx, company, res, fetch, only_if_found=True)[1]
            elif res.status != "UNREACHABLE":
                store.save_resolution(conn, pid, res)          # le site répond mais n'est plus le sien : « sans site », à chercher
                siterecheck._drop_no_site_draft(conn, pid)     # brouillon « site inaccessible » devenu faux
        store.set_provenance(conn, pid, "unreachable_retry", {"checked_at": runner._now_iso(), "tries": tries, "status": status})
        return saved
    return _loop(conn, rows, limit, deadline, one, _mark(conn, "unreachable_retry", tries=True, status="UNREACHABLE"))


def site_emails(conn, fetch, limit: int = SITE_EMAIL_PER_RUN, deadline: float | None = None) -> dict:
    """Site vérifié sans e-mail : accueil, contact et mentions légales relus (lecteurs d'adresses masquées compris), une fois par fiche ; les
    coordonnées trouvées sont AJOUTÉES (jamais remplacées). Celles sans aucun contact d'abord. Toujours sans e-mail après SITE_EMAIL_RETRY_DAYS
    jours : relue (au plus SITE_EMAIL_MAX_TRIES lectures, compteur `tries` dans la provenance `site_email`), avec en plus les pages /contact et
    /mentions-legales cherchées même sans lien."""
    if limit <= 0:
        return _out(stop="off")
    tries = f"COALESCE(CAST(JSON_UNQUOTE({_prov('site_email.tries')}) AS UNSIGNED), 1)"
    rows = _rows(conn, f"""p.website_status IN ('CONFIRMED','PROBABLE') AND COALESCE(p.site_review, '') <> 'PENDING' AND p.website_url IS NOT NULL
        AND (p.email IS NULL OR p.email = '') AND p.pipeline_stage = 'DONE' AND {BASE}
        AND ({_prov('site_email')} IS NULL OR ({tries} < {SITE_EMAIL_MAX_TRIES} AND {_date('site_email.checked_at')}
             < UTC_TIMESTAMP() - INTERVAL {SITE_EMAIL_RETRY_DAYS} DAY))""",
                 limit, f"({_prov('site_email')} IS NOT NULL), (TRIM(COALESCE(p.phone, '')) = '') DESC, {ORDER}")

    def one(row):
        from . import emailsearch
        pages, _home = sitefinder._load_pages(row["website_url"], fetch, runner._company(conn, row))
        added: list[str] = []
        prev = siterecheck._jv(row.get("provenance")).get("site_email")
        tries = (int(prev.get("tries") or 1) + 1) if isinstance(prev, dict) else 1
        if pages:
            res = SimpleNamespace(pages=pages, canonical_domain=row.get("canonical_domain") or pages[0].domain)
            _pages, added = siterecheck._site_contacts(conn, row, res, fetch, deep=tries > 1)
        store.set_provenance(conn, row["id"], "site_email", {"checked_at": runner._now_iso(), "read": bool(pages), "added": added, "tries": tries})
        if added:
            emailsearch._rescore(conn, row["id"])
        return "email" in added
    return _loop(conn, rows, limit, deadline, one, _mark(conn, "site_email", tries=True))


def finish_stuck(conn, fetch, search=None, limit: int = STUCK_PER_RUN, deadline: float | None = None) -> dict:
    """« Traitement en cours » qui ne finirait jamais : étape ≠ DONE, aucune campagne en cours pour la fiche (campagne terminée, arrêtée, en
    erreur ; reprise jamais planifiée ou dépassée). Une seule reprise par fiche (provenance `stuck_retry`), comme le runner : ce qui reste à faire
    seulement. `search=None` : fiches dont le site est déjà tranché ou cherché (aucun moteur) ; avec moteurs : celles jamais cherchées. Un nouvel
    échec avec un site déjà connu : fiche terminée avec ce qu'on sait (elle cesse d'être « en cours »)."""
    if limit <= 0:
        return _out(stop="off")
    rows = _rows(conn, f"""p.pipeline_stage NOT IN ('DONE','IMPORTED') AND p.contacted_at IS NULL AND p.do_not_contact = 0
        AND p.excluded_reason IS NULL AND p.is_chain = 0 AND NOT {RUNNING} AND {_prov('stuck_retry')} IS NULL
        AND (p.pipeline_stage <> 'ERROR' OR p.next_retry_at IS NULL OR p.next_retry_at <= UTC_TIMESTAMP())
        AND p.website_status IS {'NOT ' if search is None else ''}NULL""", limit, "p.prospect_score DESC, p.id")

    def one(row):
        pid = row["id"]
        ctx = siterecheck.ctx_for(conn, pid)
        try:
            runner.process_prospect(conn, row, ctx, {}, search, fetch)
            result = "done"
        except SearchUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête pas les autres ; marquée : jamais reprise en boucle
            conn.rollback()
            result = f"error:{errors.classify(exc)}"
            log.warning("[local] reprise de la fiche bloquée #%s en échec : %s", pid, exc)
            if row.get("website_status") is not None:
                runner._rescore(conn, db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,)), ctx)
                store.clear_error(conn, pid, "DONE")           # site déjà tranché : fiche terminée avec ce qu'on sait
                result += ":finished"
        store.set_provenance(conn, pid, "stuck_retry", {"checked_at": runner._now_iso(), "stage": row.get("pipeline_stage"), "result": result})
        return result == "done" or result.endswith(":finished")
    return _loop(conn, rows, limit, deadline, one)


# ── Avec moteurs ────────────────────────────────────────────────────────────────────────────────────
def search_more(conn, search, fetch, limit: int = SITE_SEARCH_PER_RUN, deadline: float | None = None) -> dict:
    """« Sans site » après une recherche trop maigre (confiance d'absence < ABSENCE_MIN : moteurs dégradés, recherche interrompue) : les stratégies
    pas encore essayées (toutes si la recherche d'origine était dégradée), fiche Google et devinettes comprises. Sans site sûr, la confiance
    d'absence est recalculée sur toutes les stratégies (siterecheck.raise_absence) : la fiche peut devenir « absence de site sûre »."""
    if limit <= 0:
        return _out(stop="off")
    tries = f"COALESCE(CAST(JSON_UNQUOTE({_prov('site_search_more.tries')}) AS UNSIGNED), 0)"
    rows = _rows(conn, f"""(p.website_status IS NULL OR (p.website_status = 'NOT_FOUND' AND COALESCE(p.website_absence_confidence, 0) < {ABSENCE_MIN}))
        AND p.pipeline_stage = 'DONE' AND {BASE}
        AND ({_prov('site_search_more.checked_at')} IS NULL OR {_date('site_search_more.checked_at')}
             < UTC_TIMESTAMP() - INTERVAL IF({tries} < {SEARCH_MORE_TRIES}, {SEARCH_MORE_DAYS}, {SEARCH_MORE_LATER_DAYS}) DAY)""",
                 limit, f"{tries}, {ORDER}")

    def one(row):
        pid = row["id"]
        ctx, company = siterecheck.ctx_for(conn, pid), runner._company(conn, row)
        ev = siterecheck._jv(row.get("website_evidence"))
        skip = set() if ev.get("degraded") or row.get("website_status") is None else {s for s in ev.get("strategies") or [] if isinstance(s, str)}
        suggested = runner._places_urls(conn, row)
        res = sitefinder.resolve(company, search, fetch, ctx.site_thresholds, known_urls=runner._known_urls(conn, row), dns=ctx.dns,
                                 candidate_urls=suggested, skip_strategies=skip)
        runner.record_places_check(conn, pid, res, suggested)
        _status, saved = siterecheck._conclude(conn, row, ctx, company, res, fetch)
        if not saved and row.get("website_status") is None:          # jamais cherchée (fiche terminée sans recherche) : le résultat est enregistré
            if db.fetch_one(conn, f"SELECT 1 AS x FROM local_prospects p WHERE p.id=%s AND p.website_status IS NULL AND {siterecheck.UNTOUCHABLE}", (pid,)):
                store.save_resolution(conn, pid, res)
        prev = siterecheck._jv(row.get("provenance")).get("site_search_more")
        store.set_provenance(conn, pid, "site_search_more", {"checked_at": runner._now_iso(), "tries": int((prev or {}).get("tries") or 0) + 1,
                                                             "status": res.status, "strategies": len(res.strategies_tried)})
        return saved
    return _loop(conn, rows, limit, deadline, one, _mark(conn, "site_search_more", tries=True))


# ── Orchestration et état ───────────────────────────────────────────────────────────────────────────
class Report:
    def __init__(self, mode: str):
        self.mode, self.t0, self.started = mode, time.monotonic(), time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        self.steps: list[dict] = []
        self.lock_note: str | None = None

    def step(self, conn, key: str, fn, deadline: float) -> dict:
        t = time.monotonic()
        try:
            out = fn(min(deadline, t + STEP_BUDGET_S))
        except Exception as exc:  # noqa: BLE001 — un sous-passage en échec n'empêche jamais les suivants
            conn.rollback()
            log.warning("[local] enrichissement, %s en échec : %s", key, exc)
            out = _out(stop="error", detail=f"{type(exc).__name__}: {exc}"[:120])
        out = {"key": key, **out, "ms": int((time.monotonic() - t) * 1000)}
        self.steps.append(out)
        return out

    def stopped(self) -> str:
        if self.lock_note:
            return self.lock_note
        for code in ("quota", "engines", "error", "time"):
            for s in self.steps:
                if s["stop"] == code:
                    return f"{STOP_TEXT[code]} ({s['key']}{' : ' + s['detail'] if s.get('detail') else ''})"[:160]
        if any(s["stop"] == "limit" for s in self.steps):
            return "plafonds du passage atteints : la suite au prochain passage"
        return "rien d'autre à faire"

    def save(self, conn) -> None:
        try:
            db.execute(conn, "INSERT INTO local_enrich_runs (started_at, duration_ms, mode, stopped, steps) VALUES (%s,%s,%s,%s,%s)",
                       (self.started, int((time.monotonic() - self.t0) * 1000), self.mode, self.stopped(), json.dumps(self.steps, ensure_ascii=False)))
            db.execute(conn, """DELETE FROM local_enrich_runs WHERE id < (SELECT m FROM (SELECT MIN(id) AS m FROM
                                (SELECT id FROM local_enrich_runs ORDER BY id DESC LIMIT %s) k) t)""", (KEEP_RUNS,))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — table absente avant la migration 033 : l'enrichissement continue
            conn.rollback()
            log.warning("[local] état du passage d'enrichissement non écrit : %s", exc)


def record_skipped(conn, reason: str) -> None:
    r = Report("skipped")
    r.lock_note = reason
    r.save(conn)


def free_steps(conn, report: Report, fetch, deadline: float) -> None:
    from . import companystate, emailsearch, naflabels
    report.step(conn, "company_state", lambda d: companystate.run(conn, deadline=d), deadline)   # d'abord : une fiche fermée ne coûte plus rien
    report.step(conn, "naf", lambda d: (lambda r: _out(r["examined"], r.get("labelled", 0), "nothing"))(naflabels.backfill(conn)), deadline)
    report.step(conn, "manual", lambda d: _out(emailsearch.rescore_manual(conn)), deadline)
    report.step(conn, "stuck", lambda d: finish_stuck(conn, fetch, None, deadline=d), deadline)
    report.step(conn, "site_identity", lambda d: site_identity(conn, fetch, deadline=d), deadline)   # avant tout envoi : sites d'une autre entreprise écartés

    def site(d):
        places.stop_reason = None
        r = siterecheck.places_site_check(conn, fetch=fetch, deadline=d)
        return _out(r["asked"], r["found"], r["stop"], places.stop_reason if r["stop"] == "quota" else None)
    report.step(conn, "places_site", site, deadline)

    def backfill(d):
        places.stop_reason = None
        r = siterecheck.places_backfill(conn, fetch=fetch, deadline=d)
        return _out(r["asked"], r["found"], r["stop"], places.stop_reason if r["stop"] == "quota" else None)
    report.step(conn, "places_backfill", backfill, deadline)
    report.step(conn, "site_guess", lambda d: site_guess(conn, fetch, deadline=d), deadline)
    report.step(conn, "unreachable", lambda d: unreachable_retry(conn, fetch, deadline=d), deadline)
    report.step(conn, "site_email", lambda d: site_emails(conn, fetch, deadline=d), deadline)


def engine_steps(conn, report: Report, pool, fetch, deadline: float) -> None:
    from . import emailsearch
    for key in ENGINE_STEPS:
        if report.steps and report.steps[-1]["key"] in ENGINE_STEPS and report.steps[-1]["stop"] == "engines":
            report.steps.append({"key": key, **_out(stop="engines", detail="moteurs indisponibles pour ce passage"), "ms": 0})
            continue                                       # moteurs en cooldown / budget du jour : inutile d'insister
        if key == "recheck":
            report.step(conn, key, lambda d: (lambda r: _out(r["rechecked"], r["found"], r["stop"]))(siterecheck.run(conn, pool, fetch=fetch, deadline=d)),
                        deadline)
        elif key == "search_more":
            report.step(conn, key, lambda d: search_more(conn, pool, fetch, deadline=d), deadline)
        elif key == "stuck_search":
            report.step(conn, key, lambda d: finish_stuck(conn, fetch, pool, deadline=d), deadline)
        else:
            report.step(conn, key, lambda d: (lambda r: _out(r["searched"], r["found"], r["stop"]))(emailsearch.run(conn, pool, deadline=d, manual=False)),
                        deadline)


def run(conn, mode: str = "full", fetch=None, pool_factory=None, lock_fn=None, now=time.monotonic) -> Report:
    """Un passage complet (voir la docstring du module). `pool_factory(conn)` et `lock_fn(wait_s)` : remplaçables en test."""
    from . import fetch as fetch_mod
    fetch = fetch or fetch_mod.Fetcher()
    pool_factory = pool_factory or engines.SearchPool
    lock_fn = lock_fn or (lambda wait_s: runner._acquire_lock(wait_s=wait_s))
    report = Report(mode)
    deadline = now() + TIME_BUDGET_S
    engines_done = mode != "full"
    if mode == "full":
        lock = lock_fn(0)
        if lock is not None:
            try:
                engine_steps(conn, report, pool_factory(conn), fetch, min(deadline, now() + ENGINE_BUDGET_S))
            finally:
                lock.close()
            engines_done = True
    free_steps(conn, report, fetch, deadline)
    if not engines_done:
        wait = max(0.0, min(LOCK_WAIT_S, deadline - now() - 30))
        lock = lock_fn(wait)
        if lock is None:
            report.lock_note = (f"verrou pris : une campagne tournait encore après {int(wait // 60)} min d'attente "
                                "(revérifications et recherches d'e-mail remises au passage suivant)")
            for key in ENGINE_STEPS:
                report.steps.append({"key": key, **_out(stop="lock"), "ms": 0})
        else:
            try:
                engine_steps(conn, report, pool_factory(conn), fetch, min(deadline, now() + ENGINE_BUDGET_S))
            finally:
                lock.close()
    return report


def main(argv: list[str] | None = None) -> int:
    mode = "no_engines" if "--no-engines" in (argv or []) else "full"
    elock = runner._acquire_enrich_lock()
    if elock is None:
        try:
            with db.connect() as conn:
                record_skipped(conn, "passage d'enrichissement précédent encore en cours")
        except Exception:  # noqa: BLE001
            pass
        return 0
    places.PURPOSE = "enrich"                              # réserves Google Places du jour et du mois
    try:
        with db.connect() as conn:
            report = run(conn, mode)
            report.save(conn)
        done = {s["key"]: s["done"] for s in report.steps if s["done"]}
        log.info("[local] enrichissement (%s) : %s — %s", mode, json.dumps(done), report.stopped())
    finally:
        elock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
