"""Envoi automatique des e-mails de prospection :  python -m worker.local.outbox   (toutes les 2 min)

DÉSACTIVÉ par défaut : rien ne part tant que Réglages > Envoi automatique n'est pas activé. À chaque passage :
  1. la boîte de réception est lue (réponses, refus, adresses invalides) : une réponse arrête les relances AVANT tout nouvel envoi ;
  2. les séquences sans réponse depuis 14 jours sont abandonnées (plus aucun contact) ;
  3. si l'envoi est activé, dans les heures ouvrées (jours ouvrés, hors fériés), DNS vérifié et plafond du jour non atteint :
     UN seul e-mail part (donc au plus un toutes les 2 min) — une relance due (J+3, puis J+7 en réponse au premier message), sinon le prochain premier message en file.
Adresses de l'entreprise seulement (Gmail, Orange… publiées comme contact : réglage personal_emails, activé par défaut) ; chaque message contient la ligne d'opposition. Formulaires et Facebook : jamais.
Premier message : seulement pour une fiche avec e-mail utilisable (`complete.reason`) ; une fiche en file encore incomplète (métier inconnu,
traitement en cours…) attend sans partir. L'E-MAIL D'ABORD : le site ne bloque plus l'envoi, il oriente le message (`complete.angle` : refonte,
création ou neutre). Un brouillon dont l'angle ne correspond plus à l'état du site (refonte alors que le site n'est plus vérifié, « sans site »
alors qu'un site est connu ou que l'absence n'est plus tranchée) n'est JAMAIS envoyé : effacé, fiche sortie de la file et rendue à la file de
rédaction (/rediger), qui le réécrit avec le bon angle. Un message neutre ne parle d'aucun site : aucune vérification du site avant lui.
Premier message ET chaque relance : entreprise fermée, cessée, radiée ou en procédure collective refusée
(`companystate.send_check` : état SIRENE / BODACC revérifié s'il date de plus de 7 jours ; source injoignable : rien ne part, nouvel essai
une heure plus tard). Juste avant, sans rien demander au propriétaire :
  * le domaine de l'adresse doit recevoir du courrier (MX, ou A / AAAA ; DoH, résultat positif gardé MX_TTL) : sinon l'adresse est marquée
    invalide (auto_state 'bounced', comme un e-mail revenu) et la fiche passe au téléphone / à la visite ;
  * message « sans site » : dernière vérification du site, sans moteur de recherche (domaine de l'adresse, domaines devinés d'après le nom, site
    de la fiche Google s'il n'a jamais été vérifié), bornée à PRESEND_TIMEOUT. Site sûr : enregistré comme par `siterecheck` (contacts ajoutés,
    score), brouillon « sans site » effacé, fiche sortie de la file — elle reviendra seule dans la file de rédaction, en refonte. Doute ou réseau
    en échec : pas d'envoi ce passage-ci, nouvel essai PRESEND_RETRY plus tard, PRESEND_MAX_TRIES fois au plus (ensuite : site « à trancher »,
    brouillon effacé, fiche rendue à la rédaction : le message sera neutre).
  * message « site inaccessible » : l'URL du site est rouverte juste avant (`presend_unreachable`, PRESEND_PROBE_TIMEOUT secondes au plus). Le site
    répond normalement : rien n'est envoyé, le site est revérifié sans moteur (enregistré s'il est le sien, sinon la fiche redevient « sans site »,
    à chercher), brouillon effacé, fiche sortie de la file, aucune alerte. Toujours injoignable (DNS, connexion refusée, erreur HTTP) : le message
    part. Doute (délai dépassé de notre côté, site qui bloque les robots) : reporté comme ci-dessus (PRESEND_RETRY, PRESEND_MAX_TRIES).
  * message sur un site EXISTANT (refonte, points à améliorer) : le site est relu juste avant (`presend_identity`, PRESEND_IDENTITY_TIMEOUT
    secondes au plus, sans moteur) avec la règle d'identité (`sitefinder.check_site`). Il ne passe plus (autre entreprise, site de groupe,
    maintenance, certificat invalide, aucune preuve) : rien n'est envoyé, la fiche redevient « site à trancher » (`siterecheck.demote_site` :
    brouillon effacé, sortie de la file), aucune alerte ; elle revient dans la file de rédaction, en neutre (ou en création une fois l'absence
    tranchée). Injoignable ou trop lent : reporté (PRESEND_RETRY, PRESEND_MAX_TRIES) ; au-delà, le site est mis en doute (`presend_identity.doubt`,
    plus « vérifié » pour `complete.angle`), brouillon effacé, fiche rendue à la rédaction en neutre.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import ssl
import sys
import threading

from .. import alerts, db
from ..config import env, log
from . import companystate, complete, inbox, maildns, mailer, store
from .contacts import FREEMAIL
from .store import is_do_not_contact

DNS_TTL = dt.timedelta(hours=6)
INBOX_EVERY = dt.timedelta(minutes=9)
MAX_FAILURES = 3
MX_TTL = dt.timedelta(days=7)            # domaine du destinataire qui reçoit du courrier : pas redemandé avant 7 jours
PRESEND_TIMEOUT = 45.0                   # secondes : dernière vérification du site avant un message « sans site » (passage toutes les 2 min)
PRESEND_RETRY = dt.timedelta(hours=1)    # doute ou réseau en échec : nouvel essai une heure plus tard
PRESEND_MAX_TRIES = 3
PRESEND_FRESH = dt.timedelta(days=3)     # vérification réussie (rien trouvé) : pas refaite avant 3 jours (envoi repoussé par une erreur SMTP…)
PRESEND_PROBE_TIMEOUT = 8.0              # secondes : réouverture de l'URL avant un message « site inaccessible »
PRESEND_IDENTITY_TIMEOUT = 25.0          # secondes : relecture du site (accueil, mentions légales, contact) avant un message sur un site existant
PRESEND_IDENTITY_FRESH = dt.timedelta(hours=6)   # relecture réussie : pas refaite avant 6 h (envoi repoussé par une erreur SMTP…)


def mail_config() -> dict:
    """Configuration SMTP / IMAP (variables LOCAL_*), lue à chaque passage."""
    user = env("LOCAL_SMTP_USER", "") or ""
    host = env("LOCAL_SMTP_HOST", "") or ""
    return {"host": host, "port": int(env("LOCAL_SMTP_PORT", "587") or 587), "user": user, "password": env("LOCAL_SMTP_PASSWORD", "") or "",
            "from": (env("LOCAL_MAIL_FROM", "") or user).strip().lower(), "from_name": env("LOCAL_MAIL_FROM_NAME", "") or "",
            "imap_host": env("LOCAL_IMAP_HOST", "") or host, "imap_port": int(env("LOCAL_IMAP_PORT", "993") or 993),
            "dkim_selector": env("LOCAL_DKIM_SELECTOR", "") or None}


def missing_config(cfg: dict) -> list[str]:
    return [k for k, v in (("LOCAL_SMTP_HOST", cfg["host"]), ("LOCAL_SMTP_USER", cfg["user"]), ("LOCAL_SMTP_PASSWORD", cfg["password"]),
                           ("LOCAL_MAIL_FROM", "@" in cfg["from"])) if not v]


def smtp_send(msg, cfg: dict) -> None:
    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        server = smtplib.SMTP_SSL(cfg["host"], 465, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        server.starttls(context=ctx)
    try:
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg, from_addr=cfg["from"])
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


def _setting(conn, key: str) -> dict:
    row = db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey=%s LIMIT 1", (key,))
    v = row["value_json"] if row else None
    return (json.loads(v) if isinstance(v, (str, bytes)) else v) or {}


def _state(conn) -> dict:
    row = db.fetch_one(conn, "SELECT * FROM local_mail_state WHERE id=1") or {}
    checks = row.get("checks")
    row["checks"] = (json.loads(checks) if isinstance(checks, (str, bytes)) else checks) or {}
    return row


def _save_checks(conn, checks: dict) -> None:
    db.execute(conn, """INSERT INTO local_mail_state (id, checks, checks_at) VALUES (1, %s, UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE checks=VALUES(checks), checks_at=VALUES(checks_at)""", (json.dumps(checks, ensure_ascii=False),))


def _error(conn, text: str) -> None:
    log.warning("[mail] %s", text)
    db.execute(conn, """INSERT INTO local_mail_state (id, last_error, last_error_at) VALUES (1, %s, UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE last_error=VALUES(last_error), last_error_at=VALUES(last_error_at)""", (text[:255],))
    conn.commit()
    alerts.alert("local_mail_error", f"Envoi automatique — {text}")


def abandon(conn, now: dt.datetime) -> int:
    """Sans réponse 14 jours après le premier e-mail : perdu, plus aucun contact (comme « Sans réponse » dans le suivi manuel)."""
    limit = now.astimezone(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=mailer.NO_REPLY_DAYS)
    with conn.cursor() as cur:
        n = cur.execute("""UPDATE local_prospects SET auto_state='abandoned', status=IF(status='CONTACTED', 'LOST', status),
                           response_status=IF(status='LOST' AND response_status IS NULL, 'NO_ANSWER', response_status)
                           WHERE auto_state='active' AND contacted_at <= %s""", (limit,))
    conn.commit()
    return n


def next_task(conn, now: dt.datetime, personal: bool = True) -> tuple[str, dict] | None:
    """La prochaine chose à envoyer : relance due d'abord (elle a une date), sinon le premier message en file depuis le plus longtemps dont la
    fiche est complète (`waiting`)."""
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    d1, d2 = mailer.FOLLOWUP_DAYS
    for row in db.fetch_all(conn, """SELECT * FROM local_prospects WHERE auto_state='active' AND do_not_contact=0 AND status='CONTACTED'
                                AND ((followups=0 AND contacted_at <= %s)
                                  OR (followups=1 AND contacted_at <= %s AND (last_followup_at IS NULL OR last_followup_at <= %s)))
                                ORDER BY contacted_at LIMIT 20""",
                            (t - dt.timedelta(days=d1), t - dt.timedelta(days=d2), t - dt.timedelta(days=d2 - d1))):
        if not companystate.send_retry_pending(row, now):        # état officiel pas vérifiable il y a moins d'une heure : les autres d'abord
            return ("followup1" if int(row["followups"] or 0) == 0 else "followup2"), row
    for row in db.fetch_all(conn, "SELECT * FROM local_prospects WHERE auto_state='queued' ORDER BY auto_queued_at, id LIMIT 300"):
        if waiting(row, personal, now) is None:
            return "first", row
    return None


def _iso(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sans_site(p: dict) -> bool:
    return "sans_site" in (p.get("draft_kind") or "")


def waiting(p: dict, personal: bool, now: dt.datetime) -> str | None:
    """Pourquoi ce premier message en file ne part pas ce passage-ci (None = il peut partir) : fiche incomplète (elle attend que l'app la
    complète), ou vérification du site à reprendre plus tard. Le site « à trancher » n'est plus une attente (il oriente le message) ; le site de
    la fiche Google encore à vérifier est vérifié juste avant un message « sans site » (`presend_site`), si Google Places est actif."""
    pre = complete._json(p.get("provenance")).get("presend_site")
    if isinstance(pre, dict) and (pre.get("retry_at") or "") > _iso(now):
        return "vérification du site à reprendre"
    if companystate.send_retry_pending(p, now):
        return "état officiel de l'entreprise à revérifier"
    # adresse refusée par le réglage : `mailer.ineligible` retire la fiche de la file, avec sa raison (pas d'attente sans fin)
    # Brouillon de type inconnu (écrit à la main, sans angle) : on ne sait pas s'il parle d'un site, il attend donc un site tranché comme avant.
    known = complete.draft_angle(p.get("draft_kind")) is not None
    codes = [c for c in complete.missing(p, personal) if c not in ("contact", "email_invalide") and not (c == "site" and known)]
    if codes:
        return "fiche incomplète : " + ", ".join(complete.LABELS[c] for c in codes)
    if _sans_site(p) and complete.site_state(p) == "places":
        from . import places
        if not places.enabled():
            return "fiche incomplète : " + complete.LABELS["site"]
    return None


def _stop(conn, p: dict, reason: str, state: str = "stopped") -> None:
    log.info("[mail] prospect %s retiré de l'envoi automatique : %s", p["id"], reason)
    db.execute(conn, "UPDATE local_prospects SET auto_state=%s WHERE id=%s", (state, p["id"]))
    conn.commit()


def mail_domain_ok(conn, email: str, now: dt.datetime, query=maildns.doh) -> bool | None:
    """Le domaine de l'adresse reçoit-il du courrier ? True / False, ou None si le DNS ne répond pas (rien conclu). Messageries gratuites
    connues : pas de requête. Résultat positif gardé MX_TTL dans l'état de l'envoi (onglet Envois)."""
    dom = mailer.email_domain(email)
    if dom in FREEMAIL:
        return True
    checks = _state(conn)["checks"]
    cache = checks.get("mx") if isinstance(checks.get("mx"), dict) else {}
    try:
        if dom in cache and now - dt.datetime.fromisoformat(cache[dom]) < MX_TTL:
            return True
    except (TypeError, ValueError):
        pass
    try:
        ok = maildns.mail_host(dom, query=query)
    except RuntimeError as exc:
        log.warning("[mail] serveur de messagerie de %s : vérification impossible (%s)", dom, exc)
        return None
    if ok:
        limit = now - MX_TTL
        fresh = {}
        for d, at in cache.items():
            try:
                if dt.datetime.fromisoformat(at) >= limit:
                    fresh[d] = at
            except (TypeError, ValueError):
                pass
        checks["mx"] = {**fresh, dom: now.isoformat()}
        _save_checks(conn, checks)
    return ok


def _mark_invalid(conn, p: dict, email: str, why: str, now: dt.datetime) -> None:
    """Adresse inutilisable AVANT tout envoi : marquée invalide comme un e-mail revenu (auto_state 'bounced'), sans alerte. La fiche passe
    au téléphone / à la visite (fiche complète « téléphone » si elle en a un)."""
    log.info("[mail] prospect %s : adresse %s invalide (%s), pas d'envoi", p["id"], email, why)
    db.execute(conn, "UPDATE local_prospects SET auto_state='bounced' WHERE id=%s AND auto_state='queued'", (p["id"],))
    store.set_provenance(conn, p["id"], "email_invalid", {"email": email, "reason": why, "at": _iso(now)})
    conn.commit()


def _defer(conn, p: dict, now: dt.datetime, why: str) -> None:
    """Pas d'envoi ce passage-ci : nouvel essai PRESEND_RETRY plus tard, PRESEND_MAX_TRIES fois au plus. Au-delà, la fiche sort de la file et
    son site reste « à trancher » (doute posé) : jamais de message « sans site » sur un doute."""
    prev = complete._json(p.get("provenance")).get("presend_site")
    tries = int((prev or {}).get("tries") or 0) + 1 if isinstance(prev, dict) else 1
    flag = {"at": _iso(now), "result": "retry", "why": why[:160], "tries": tries, "retry_at": _iso(now + PRESEND_RETRY)}
    if tries >= PRESEND_MAX_TRIES:
        flag = {**flag, "result": "doubt", "doubt": True, "retry_at": None}
        db.execute(conn, "UPDATE local_prospects SET auto_state=NULL, auto_queued_at=NULL WHERE id=%s AND auto_state='queued'", (p["id"],))
        angle = complete.draft_angle(p.get("draft_kind"))
        if angle == "refonte" and (p.get("website_url") or "").strip():
            # site existant jamais relu à temps : plus « vérifié » (complete.site_verified), le prochain message sera neutre
            store.set_provenance(conn, p["id"], "presend_identity", {"at": _iso(now), "result": "doubt", "doubt": True,
                                                                     "url": p["website_url"].strip()[:200], "why": why[:160]})
        if angle in ("refonte", "creation"):
            _redraft(conn, p, "site pas vérifiable avant l'envoi")      # le brouillon parle d'un site (ou de son absence) : réécrit en neutre
        log.info("[mail] prospect %s sorti de la file après %d essais : %s (site à trancher)", p["id"], tries, why)
    else:
        log.info("[mail] prospect %s : envoi repoussé (%s), essai %d/%d", p["id"], why, tries, PRESEND_MAX_TRIES)
    store.set_provenance(conn, p["id"], "presend_site", flag)
    conn.commit()


def _redraft(conn, p: dict, why: str) -> None:
    """Brouillon dont l'angle ne correspond plus à l'état du site : effacé, fiche sortie de la file (jamais une fiche contactée ou en séquence).
    Elle revient dans la file de rédaction (/rediger, fiches sans brouillon), où le brouillon de référence prend l'angle du moment."""
    from .siterecheck import _drop_no_site_draft
    _drop_no_site_draft(conn, p["id"])
    log.info("[mail] prospect %s : brouillon %s effacé (%s), à rédiger de nouveau en %s", p["id"], p.get("draft_kind") or "?", why,
             complete.angle(p))


def _resolve_bounded(company: dict, fetch, thresholds, known: list[str], dns, urls: list[str], timeout: float):
    """`sitefinder.resolve` SANS moteur de recherche (domaine de l'adresse, devinettes, site de la fiche Google), dans un fil borné à `timeout`.
    Renvoie la résolution, ou None si le temps est écoulé ou en cas d'erreur (rien n'est conclu)."""
    from . import siterecheck, sitefinder
    out: list = []

    def work():
        try:
            out.append(sitefinder.resolve(company, siterecheck._no_search, fetch, thresholds, strategy_budget=0, known_urls=known, dns=dns,
                                          candidate_urls=urls))
        except Exception as exc:  # noqa: BLE001 — une erreur de lecture = rien de conclu
            log.warning("[mail] vérification du site en échec : %s", exc)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return out[0] if out else None


def presend_site(conn, p: dict, now: dt.datetime, fetch=None, timeout: float = PRESEND_TIMEOUT) -> tuple[str, str]:
    """Dernière vérification du site avant un message « sans site ». Renvoie (verdict, détail) : 'none' (rien trouvé : le message peut
    partir), 'found' (site sûr enregistré comme par siterecheck, brouillon effacé, fiche sortie de la file), 'retry' (doute ou erreur)."""
    from . import places, runner, siterecheck
    pid = p["id"]
    prov = complete._json(p.get("provenance"))
    pre = prov.get("presend_site") if isinstance(prov.get("presend_site"), dict) else {}
    places_due = complete.site_state(p) == "places"
    try:
        if pre.get("result") == "none" and not places_due and now - dt.datetime.fromisoformat(pre["at"].replace("Z", "+00:00")) < PRESEND_FRESH:
            return "none", "déjà vérifié"
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    if fetch is None:
        from . import fetch as fetch_mod
        fetch = fetch_mod.Fetcher(timeout=6.0)
    camp = siterecheck._campaign(conn, pid) or {"radius_km": 30}
    ctx, company = runner.Ctx(conn, camp), runner._company(conn, p)
    urls: list[str] = []
    if places_due:
        url, concluded = places.recover_url(conn, p, company)        # une requête Place Details (budget du mois) ; l'URL n'est jamais stockée
        if not concluded:
            conn.rollback()
            return "retry", "site de la fiche Google pas encore vérifiable"
        urls = [url] if url else []
    res = _resolve_bounded(company, fetch, ctx.site_thresholds, runner._known_urls(conn, p), ctx.dns, urls, timeout)
    if res is None:
        return "retry", "vérification du site trop longue ou en échec"
    runner.record_places_check(conn, pid, res, urls)
    if res.accepted:
        try:
            _status, saved = siterecheck._conclude(conn, p, ctx, company, res, fetch, only_if_found=True)
        except Exception as exc:  # noqa: BLE001 — enregistrement en échec : rien d'envoyé, nouvel essai plus tard
            conn.rollback()
            return "retry", f"enregistrement du site en échec ({type(exc).__name__})"
        if saved:
            conn.commit()
            return "found", res.url or ""
        conn.rollback()
        return "retry", "fiche modifiée pendant la vérification"
    dom = mailer.email_domain(p.get("email"))
    if res.status == "UNCERTAIN" or any(c.get("domain") == dom and c.get("reachable") and not c.get("parked") for c in res.candidates
                                        if dom and dom not in FREEMAIL):
        conn.commit()                                                 # verdict Google éventuel gardé
        return "retry", f"site possible ({res.candidates[0]['domain'] if res.candidates else '?'}), pas assez sûr"
    store.set_provenance(conn, pid, "presend_site", {"at": _iso(now), "result": "none", "tries": int(pre.get("tries") or 0)})
    conn.commit()
    return "none", ""


def presend_email_domain(conn, p: dict, now: dt.datetime, fetch=None, timeout: float = PRESEND_TIMEOUT) -> tuple[str, str]:
    """Garde-fou avant un message « sans site » : le domaine PROFESSIONNEL de l'adresse e-mail héberge-t-il un vrai site ? (contact@club.com →
    club.com : « je n'ai pas trouvé de site » serait faux.) Lecture sans moteur dans un fil borné (`siterecheck.email_domain_site`). Renvoie
    (verdict, détail) : 'none' (domaine sans site exploitable, ou messagerie gratuite : le message peut partir), 'found' (site retenu), 'uncertain'
    (le site répond sans preuve suffisante : fiche « à trancher »), 'retry' (trop lent ou en échec). Rien trouvé : noté 3 jours (PRESEND_FRESH)."""
    from . import runner, siterecheck, sitefinder
    company = runner._company(conn, p)
    dom = sitefinder.email_proof_domain(company)
    if not dom:
        return "none", ""
    prev = complete._json(p.get("provenance")).get("email_domain_site")
    try:
        if isinstance(prev, dict) and prev.get("domain") == dom and prev.get("status") == "NOT_FOUND" \
                and now - dt.datetime.fromisoformat(str(prev["checked_at"]).replace("Z", "+00:00")) < PRESEND_FRESH:
            return "none", "déjà vérifié"
    except (KeyError, TypeError, ValueError):
        pass
    if fetch is None:
        from . import fetch as fetch_mod
        fetch = fetch_mod.Fetcher(timeout=6.0)
    ctx = siterecheck.ctx_for(conn, p["id"])
    out: list = []

    def work():
        try:
            out.append(sitefinder.resolve_email_domain(company, fetch, ctx.site_thresholds))
        except Exception as exc:  # noqa: BLE001 — une erreur de lecture = rien de conclu
            log.warning("[mail] site du domaine de l'e-mail en échec : %s", exc)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if not out:
        return "retry", "vérification du domaine de l'e-mail trop longue ou en échec"
    res = out[0]
    try:
        status, saved = siterecheck.email_domain_site(conn, p, ctx, fetch, res=res)
    except Exception as exc:  # noqa: BLE001 — enregistrement en échec : rien d'envoyé, nouvel essai plus tard
        conn.rollback()
        return "retry", f"enregistrement du site en échec ({type(exc).__name__})"
    conn.commit()
    if saved:
        return "found", res.url or ""
    if res.status == "UNCERTAIN":
        return "uncertain", res.candidates[0]["url"] if res.candidates else ""
    return "none", ""


def _site_down_draft(p: dict) -> bool:
    return "site_inaccessible" in (p.get("draft_kind") or "")


def _probe_bounded(fetch, url: str, timeout: float):
    """UNE lecture de l'URL dans un fil borné à `timeout`. Renvoie (réponse ou None, terminé à temps ?)."""
    out: list = []

    def work():
        try:
            out.append(fetch(url))
        except Exception as exc:  # noqa: BLE001 — une erreur de lecture = site injoignable (le Fetcher ne lève jamais)
            log.info("[mail] lecture de %s en échec : %s", url[:100], exc)
            out.append(None)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return (out[0] if out else None), bool(out)


def presend_unreachable(conn, p: dict, now: dt.datetime, fetch=None, timeout: float | None = None) -> tuple[str, str]:
    """Dernière vérification avant un message « site inaccessible ». Renvoie (verdict, détail) : 'down' (toujours injoignable : le message peut
    partir), 'up' (le site répond : revérifié sans moteur, brouillon effacé, fiche sortie de la file, rien d'envoyé), 'retry' (doute)."""
    from . import errors, runner, siterecheck
    timeout = PRESEND_PROBE_TIMEOUT if timeout is None else timeout
    url = (p.get("website_url") or "").strip()
    if not url:
        return "down", "aucune adresse de site connue"
    if fetch is None:
        from . import fetch as fetch_mod
        fetch = fetch_mod.Fetcher(timeout=min(6.0, timeout))
    before = len(getattr(fetch, "failures", []) or [])
    got, finished = _probe_bounded(fetch, url, timeout)
    if not finished:
        return "retry", "site trop lent à répondre (délai dépassé de notre côté)"
    cats = {c for c, _u in (getattr(fetch, "failures", []) or [])[before:]}
    if got is None:
        if errors.SITE_TIMEOUT in cats:
            return "retry", "délai dépassé en lisant le site (réseau de notre côté ?)"
        return "down", ", ".join(sorted(cats)) or "injoignable"
    status = int(getattr(got, "status", 0) or 0)
    if status in (401, 403, 429) or errors.SITE_BLOCKED in cats and status != 503:
        return "retry", f"le site refuse les robots (HTTP {status}) : peut-être joignable"
    if status >= 400 or not getattr(got, "text", ""):
        return "down", f"HTTP {status}"
    # le site répond normalement : le message serait faux. Revérifié sans moteur, comme enrich.unreachable_retry.
    pid = p["id"]
    camp = siterecheck._campaign(conn, pid) or siterecheck.DEFAULT_CAMPAIGN
    ctx, company = runner.Ctx(conn, camp), runner._company(conn, p)
    res = _resolve_bounded(company, fetch, ctx.site_thresholds, runner._known_urls(conn, p), None, [url], PRESEND_TIMEOUT)
    try:
        if res is not None and res.accepted:
            siterecheck._conclude(conn, p, ctx, company, res, fetch, only_if_found=True)
        elif res is not None and res.status != "UNREACHABLE":
            store.save_resolution(conn, pid, res)                     # répond mais n'est plus le sien : « sans site », à chercher
        # sinon (vérification trop longue) : statut inchangé, revu par enrich.unreachable_retry ; le brouillon, lui, est faux
        siterecheck._drop_no_site_draft(conn, pid)                    # brouillon « site inaccessible » effacé, fiche sortie de la file
        store.set_provenance(conn, pid, "presend_site", {"at": _iso(now), "result": "site_up", "url": url[:200],
                                                         "status": res.status if res is not None else None})
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — enregistrement en échec : rien d'envoyé, nouvel essai plus tard
        conn.rollback()
        return "retry", f"enregistrement en échec ({type(exc).__name__})"
    return "up", url


def presend_identity(conn, p: dict, now: dt.datetime, fetch=None, timeout: float | None = None) -> tuple[str, str]:
    """Dernière vérification avant un premier message sur un site EXISTANT : le site répond-il, et passe-t-il toujours la règle d'identité ?
    Renvoie (verdict, détail) : 'ok' (le message peut partir), 'demoted' (ne passe plus : fiche « à trancher », brouillon effacé, sortie de la
    file, rien d'envoyé), 'retry' (injoignable, trop lent, erreur : reporté)."""
    from . import runner, siterecheck, sitefinder
    timeout = PRESEND_IDENTITY_TIMEOUT if timeout is None else timeout
    url = (p.get("website_url") or "").strip()
    if not url:
        return "retry", "aucune adresse de site connue"
    if p.get("site_review") == "CONFIRMED":
        return "ok", "site confirmé par toi"
    prev = complete._json(p.get("provenance")).get("presend_identity")
    try:
        if isinstance(prev, dict) and prev.get("result") == "ok" and prev.get("url") == url[:200] \
                and now - dt.datetime.fromisoformat(str(prev["at"]).replace("Z", "+00:00")) < PRESEND_IDENTITY_FRESH:
            return "ok", "déjà vérifié"
    except (KeyError, TypeError, ValueError):
        pass
    if fetch is None:
        from . import fetch as fetch_mod
        fetch = fetch_mod.Fetcher(timeout=6.0)
    pid = p["id"]
    ctx, company = siterecheck.ctx_for(conn, pid), runner._company(conn, p)
    out: list = []

    def work():
        try:
            out.append(sitefinder.check_site(company, url, fetch, ctx.site_thresholds))
        except Exception as exc:  # noqa: BLE001 — une erreur de lecture = rien de conclu
            log.warning("[mail] relecture du site de %s en échec : %s", pid, exc)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if not out:
        return "retry", "relecture du site trop longue ou en échec"
    verdict, label, a = out[0]
    if verdict == "unreachable":
        return "retry", "le site ne répond pas"
    flag = {"at": _iso(now), "result": verdict, "url": url[:200], "version": sitefinder.IDENTITY_VERSION}
    if verdict == "ok":
        store.set_provenance(conn, pid, "presend_identity", flag)
        conn.commit()
        return "ok", ""
    try:
        done = siterecheck.demote_site(conn, p, verdict, label, a, ctx)
        store.set_provenance(conn, pid, "presend_identity", {**flag, "label": label[:200], **(done or {})})
        store.set_provenance(conn, pid, "site_identity", {"checked_at": _iso(now), "version": sitefinder.IDENTITY_VERSION, "result": verdict,
                                                          "label": label[:200], "url": url[:200], "via": "presend", **(done or {})})
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — enregistrement en échec : rien d'envoyé, nouvel essai plus tard
        conn.rollback()
        return "retry", f"enregistrement en échec ({type(exc).__name__})"
    if done is None:
        return "retry", "fiche modifiée pendant la vérification"
    return "demoted", f"{verdict} : {label}"


def send_one(conn, kind: str, p: dict, cfg: dict, sender: dict, now: dt.datetime, send=smtp_send, sent_copy=None, personal: bool = False,
             mx_query=maildns.doh, site_fetch=None) -> str:
    """Envoie UN e-mail. Renvoie 'sent', 'error' (problème du serveur : on réessaiera) ou, sans envoi : 'skipped' (prospect retiré),
    'found_site' (site trouvé juste avant), 'waiting' (fiche incomplète, reste en file) ou 'deferred' (vérification à reprendre plus tard)."""
    from_name = cfg["from_name"] or sender.get("name") or ""
    if kind == "first":
        reason = mailer.ineligible(p, personal)
        email = (p.get("email") or "").strip().lower()
        if not reason and is_do_not_contact(conn, p.get("siret"), p.get("siren"), p.get("canonical_domain"), email):
            reason = "ne plus contacter"
        if not reason and (p.get("contacted_at") or db.fetch_one(conn, "SELECT 1 AS x FROM local_emails WHERE to_email=%s AND status<>'failed' LIMIT 1", (email,))):
            reason = "déjà contacté"
        if reason:
            _stop(conn, p, reason)
            return "skipped"
        # Entreprise fermée, cessée, radiée ou en procédure collective (état officiel revérifié s'il date de plus de SEND_DAYS jours) :
        # jamais de premier message ; la fiche sort de la file, son brouillon reste (plus proposé : elle est écartée).
        state, why = companystate.send_check(conn, p, now)
        if state == "closed":
            db.execute(conn, "UPDATE local_prospects SET auto_queued_at=NULL, auto_state=NULL WHERE id=%s AND auto_state='queued'", (p["id"],))
            conn.commit()
            log.info("[mail] prospect %s retiré de l'envoi automatique : %s", p["id"], why)
            return "skipped"
        if state == "retry":
            log.info("[mail] prospect %s : envoi repoussé (%s)", p["id"], why)
            return "deferred"
        # Brouillon qui ne correspond plus à l'état du site (« sans site » alors que le site est connu depuis ou que l'absence n'est plus
        # tranchée, refonte alors que le site n'est plus vérifié…) : jamais envoyé ; effacé, sortie de la file, rendu à la rédaction.
        # Un brouillon neutre ne parle d'aucun site : il part quel que soit le site.
        d_angle = complete.draft_angle(p.get("draft_kind"))
        if d_angle in ("refonte", "creation") and complete.angle(p) != d_angle:
            _redraft(conn, p, f"brouillon {d_angle}, site désormais « {complete.angle(p)} »")
            conn.commit()
            return "skipped"
        ok = mail_domain_ok(conn, email, now, query=mx_query)
        if ok is False:
            _mark_invalid(conn, p, email, f"aucun serveur de messagerie pour {mailer.email_domain(email)}", now)
            return "skipped"
        if ok is None:
            _defer(conn, p, now, "DNS du destinataire injoignable")
            return "deferred"
        if _sans_site(p):
            verdict, detail = presend_email_domain(conn, p, now, fetch=site_fetch)
            if verdict in ("found", "uncertain"):
                _redraft(conn, p, f"le domaine de l'e-mail héberge un site ({detail})")      # le message de création serait faux : repart en rédaction
                conn.commit()
                log.info("[mail] prospect %s : site sur le domaine de son e-mail (%s), message « sans site » effacé, rien d'envoyé", p["id"], detail)
                return "found_site"
            if verdict == "retry":
                _defer(conn, p, now, detail)
                return "deferred"
            verdict, detail = presend_site(conn, p, now, fetch=site_fetch)
            if verdict == "found":
                log.info("[mail] prospect %s : site trouvé avant l'envoi (%s), message « sans site » effacé, rien d'envoyé", p["id"], detail)
                return "found_site"
            if verdict == "retry":
                _defer(conn, p, now, detail)
                return "deferred"
            p = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (p["id"],)) or p   # verdict Google éventuel posé
            if complete.angle(p) != "creation":
                _redraft(conn, p, "absence de site plus tranchée après la dernière vérification")
                conn.commit()
                return "skipped"
        elif _site_down_draft(p):
            verdict, detail = presend_unreachable(conn, p, now, fetch=site_fetch)
            if verdict == "up":
                log.info("[mail] prospect %s : le site répond de nouveau (%s), message « site inaccessible » effacé, rien d'envoyé", p["id"], detail)
                return "found_site"
            if verdict == "retry":
                _defer(conn, p, now, detail)
                return "deferred"
        elif d_angle != "neutre" and p.get("website_status") in ("CONFIRMED", "PROBABLE") and p.get("website_url"):
            verdict, detail = presend_identity(conn, p, now, fetch=site_fetch)
            if verdict == "demoted":
                log.info("[mail] prospect %s : le site ne passe plus la vérification d'identité (%s), brouillon effacé, rien d'envoyé", p["id"], detail)
                return "found_site"                    # comme un site retrouvé : la vérification a pris du temps, la suite au passage suivant
            if verdict == "retry":
                _defer(conn, p, now, detail)
                return "deferred"
        if (why := complete.reason(p, personal)) is not None or (d_angle is None and "site" in complete.missing(p, personal)
                                                                 and (why := "brouillon écrit à la main : site à trancher")):
            log.info("[mail] prospect %s en attente : %s", p["id"], why)
            return "waiting"
        subject, text, to, reply_to = p["draft_subject"].strip(), p["draft_message"], email, None
    else:
        first = db.fetch_one(conn, "SELECT * FROM local_emails WHERE prospect_id=%s AND kind='first' AND status='sent' ORDER BY sent_at LIMIT 1", (p["id"],))
        if not first or is_do_not_contact(conn, p.get("siret"), p.get("siren"), p.get("canonical_domain"), first["to_email"]):
            _stop(conn, p, "premier e-mail introuvable ou opposition")
            return "skipped"
        state, why = companystate.send_check(conn, p, now)        # fermée / en procédure depuis le premier message : plus aucune relance
        if state == "closed":
            _stop(conn, p, f"entreprise fermée ou en procédure : {why}")
            return "skipped"
        if state == "retry":
            log.info("[mail] relance au prospect %s repoussée (%s)", p["id"], why)
            return "deferred"
        subject, to, reply_to = mailer.reply_subject(first["subject"]), first["to_email"], first["message_id"]
        text = mailer.followup_body(kind, p, first, now, from_name)
    msg = mailer.build_message(from_addr=cfg["from"], from_name=from_name, to=to, subject=subject, text=text, sender=sender, in_reply_to=reply_to)
    try:
        send(msg, cfg)
    except smtplib.SMTPRecipientsRefused as exc:
        db.execute(conn, """INSERT INTO local_emails (prospect_id, kind, to_email, subject, body, message_id, in_reply_to, status, error)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,'failed',%s)""", (p["id"], kind, to, subject[:255], text, msg["Message-ID"], reply_to, str(exc)[:255]))
        _stop(conn, p, "adresse refusée par le serveur", "bounced")
        return "skipped"
    except smtplib.SMTPDataError as exc:
        # Message refusé (contenu, filtre) : propre à ce prospect. Après MAX_FAILURES refus, il sort de l'envoi automatique.
        db.execute(conn, """INSERT INTO local_emails (prospect_id, kind, to_email, subject, body, message_id, in_reply_to, status, error)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,'failed',%s)""", (p["id"], kind, to, subject[:255], text, msg["Message-ID"], reply_to, str(exc)[:255]))
        conn.commit()
        fails = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_emails WHERE prospect_id=%s AND status='failed'", (p["id"],))["n"]
        if fails >= MAX_FAILURES:
            _stop(conn, p, f"{fails} refus du serveur d'envoi", "failed")
        _error(conn, f"message refusé par le serveur ({exc.smtp_code})")
        return "error"
    except smtplib.SMTPAuthenticationError as exc:
        # Jamais la valeur : seulement sa longueur, pour repérer un mot de passe coupé (« # » ou « $ » mal lus dans le .env de Dokploy).
        _error(conn, f"identifiant ou mot de passe refusé par le serveur ({exc.smtp_code}) — mot de passe lu : {len(cfg['password'])} caractères, "
                     f"utilisateur : {cfg['user']}")
        return "error"
    except (smtplib.SMTPException, OSError) as exc:
        _error(conn, f"envoi impossible ({type(exc).__name__}) : {exc}")
        return "error"
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)              # UTC naïf, comme toute la base
    db.execute(conn, """INSERT INTO local_emails (prospect_id, kind, to_email, subject, body, message_id, in_reply_to, status, sent_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,'sent',%s)""", (p["id"], kind, to, subject[:255], text, msg["Message-ID"], reply_to, t))
    if kind == "first":
        db.execute(conn, """UPDATE local_prospects SET status='CONTACTED', contacted_at=%s, last_contacted_at=%s, followups=0,
                            last_followup_at=NULL, auto_state='active' WHERE id=%s""", (t, t, p["id"]))
    else:
        db.execute(conn, "UPDATE local_prospects SET followups=followups+1, last_followup_at=%s, last_contacted_at=%s WHERE id=%s", (t, t, p["id"]))
    db.execute(conn, "UPDATE local_mail_state SET last_error=NULL, last_error_at=NULL WHERE id=1")   # un envoi réussi efface l'erreur affichée
    conn.commit()
    log.info("[mail] %s envoyé au prospect %s", kind, p["id"])
    if sent_copy:
        sent_copy(msg)
    return "sent"


def run(conn, now: dt.datetime | None = None, send=smtp_send, imap=inbox.connect, dns=maildns.doh_txt, mx_query=maildns.doh, site_fetch=None) -> dict:
    """Un passage. `dns` (TXT du domaine d'envoi), `mx_query` (DNS du destinataire) et `site_fetch` (lecture des sites) : remplaçables en test."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg, out = mail_config(), {}
    missing = missing_config(cfg)
    # Passage toutes les 2 min : la boîte est lue toutes les INBOX_EVERY au plus, et TOUJOURS avant une relance (jamais de relance à
    # quelqu'un qui vient de répondre).
    last = _state(conn).get("imap_at")
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    task = next_task(conn, now)
    if not missing and (last is None or t - last >= INBOX_EVERY or t < last or (task and task[0] != "first")):
        try:
            out["inbox"] = inbox.poll(conn, cfg, factory=imap)
        except Exception as exc:     # noqa: BLE001 — une boîte illisible n'empêche pas d'abandonner les séquences, mais bloque les relances
            conn.rollback()
            _error(conn, f"lecture de la boîte impossible ({type(exc).__name__}) : {exc}")
            out["inbox_error"] = True
    out["abandoned"] = abandon(conn, now)
    auto = mailer.settings(_setting(conn, "local_auto_send"))
    checks = _state(conn)["checks"]
    checks["config_missing"] = missing
    if not auto["enabled"]:
        _save_checks(conn, checks)
        conn.commit()
        return {**out, "status": "disabled"}
    if missing:
        _save_checks(conn, checks)
        conn.commit()
        return {**out, "status": "config"}
    domain, prev = mailer.email_domain(cfg["from"]), checks.get("dns") or {}
    try:
        age = now - dt.datetime.fromisoformat(prev["at"])
    except (KeyError, TypeError, ValueError):
        age = None
    if prev.get("domain") != domain or prev.get("selector") != cfg["dkim_selector"] or age is None or not dt.timedelta(0) <= age < DNS_TTL:
        checks["dns"] = {**maildns.check(domain, cfg["dkim_selector"], txt=dns), "domain": domain, "selector": cfg["dkim_selector"], "at": now.isoformat()}
    _save_checks(conn, checks)
    conn.commit()
    if not checks["dns"]["ok"]:
        alerts.alert("local_mail_dns", "Envoi automatique en attente — DNS : " + " ; ".join(checks["dns"]["problems"]))
        return {**out, "status": "dns"}
    if out.get("inbox_error"):
        return {**out, "status": "inbox"}            # sans lecture de la boîte, on ne risque pas une relance à quelqu'un qui a répondu
    if not mailer.in_window(now, auto):
        return {**out, "status": "hours"}
    sent_today = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_emails WHERE status IN ('sent','bounced') AND sent_at >= %s",
                              (mailer.paris_midnight_utc(now),))["n"]
    if sent_today >= auto["daily_cap"]:
        return {**out, "status": "cap"}
    sender = _setting(conn, "local_sender")
    for _ in range(5):                  # quelques prospects retirés (opposition, adresse perso…) ne gâchent pas le passage
        task = next_task(conn, now, auto["personal_emails"])
        if not task:
            return {**out, "status": "idle"}
        result = send_one(conn, task[0], task[1], cfg, sender, now, send=send,
                          sent_copy=lambda m: inbox.append_sent(cfg, m.as_bytes(), factory=imap), personal=auto["personal_emails"],
                          mx_query=mx_query, site_fetch=site_fetch)
        if result not in ("skipped", "waiting"):     # 'found_site' / 'deferred' : une vérification a déjà pris du temps, la suite au passage suivant
            return {**out, "status": result, "kind": task[0]}
    return {**out, "status": "idle"}


def _lock():
    """Un seul passage à la fois : la vérification d'un site avant l'envoi peut durer plus que l'intervalle de 2 min (jamais deux envois du même
    message). Dossier des journaux absent (poste de développement) : pas de verrou."""
    import fcntl
    try:
        fh = open(os.path.join(os.path.dirname(os.getenv("LOCAL_LOCK", "/app/logs/local.lock")), "outbox.lock"), "w")
    except OSError:
        return open(os.devnull, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main() -> int:
    lock = _lock()
    if lock is None:
        log.info("[mail] passage précédent encore en cours")
        return 0
    try:
        with db.connect() as conn:
            result = run(conn)
    finally:
        lock.close()
    if result.get("status") not in ("disabled", "idle", "hours", "cap"):
        log.info("[mail] passage : %s", result)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
