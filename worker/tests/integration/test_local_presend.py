"""Vérification du site JUSTE AVANT un message « sans site » (outbox.presend_site) sur un VRAI MySQL : site retrouvé par une devinette de domaine
→ rien n'est envoyé, le site est enregistré comme par siterecheck (contacts ajoutés), le brouillon effacé, la fiche sortie de la file, aucune
alerte ; rien trouvé → le message part ; doute ou délai dépassé → pas d'envoi, nouvel essai plus tard, puis sortie de la file. Sites, moteurs,
DNS et SMTP simulés (aucune requête réelle)."""
from __future__ import annotations

import datetime as dt
import json

from worker import db
from worker.local import complete, outbox, sitefinder

from local_fixtures import html

from .test_local_mail import BODY, T0, mail, row  # noqa: F401 — fixture `mail` réutilisée
from .test_local_siterecheck import DUPONT, SITE, _setup

__all__ = ["mail"]


def _queue_dupont(conn, monkeypatch, **extra):
    web, search = _setup(conn, monkeypatch)
    sets = {"email": "boulangerie.dupont@gmail.com", "email_kind": "UNCERTAIN", "website_absence_confidence": 0.7,
            "activity_label": "Boulangeries, pâtisseries", "draft_message": BODY, **extra}
    db.execute(conn, f"UPDATE local_prospects SET {', '.join(f'{k}=%s' for k in sets)} WHERE siret=%s", (*sets.values(), DUPONT))
    db.execute(conn, """UPDATE local_prospects SET provenance = JSON_SET(COALESCE(provenance, JSON_OBJECT()), '$.site_recheck', JSON_OBJECT('status', 'NOT_FOUND'))
                        WHERE siret=%s""", (DUPONT,))
    conn.commit()
    p = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE siret=%s", (DUPONT,))
    assert complete.missing(p, True) == [], complete.missing(p, True)               # fiche complète : seule la vérification d'avant l'envoi l'arrête
    return p["id"], web


def test_site_retrouve_avant_l_envoi_rien_ne_part(mail, conn, monkeypatch):
    pid, web = _queue_dupont(conn, monkeypatch)
    mail.enable()
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [d for d in ds if d == SITE])
    r = mail.run(T0, fetch=web)
    p = row(conn, pid)
    assert r["status"] == "found_site" and mail.smtp.sent == [] and mail.tg.texts == []        # aucun envoi, aucune alerte
    assert p["website_status"] == "CONFIRMED" and p["website_url"] == f"https://{SITE}/"
    assert p["draft_message"] is None and p["draft_kind"] is None and p["auto_state"] is None and p["contacted_at"] is None
    assert p["email"] == "boulangerie.dupont@gmail.com" and p["phone"] == "0325998877"           # contacts connus gardés
    assert json.loads(p["provenance"])["site_recheck"]["draft_dropped"] is True and p["scored_at"] is not None


def test_rien_trouve_le_message_part(mail, conn, monkeypatch):
    pid, web = _queue_dupont(conn, monkeypatch)
    mail.enable()
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])
    r = mail.run(T0, fetch=web)
    assert r["status"] == "sent" and mail.smtp.sent[0]["To"] == "boulangerie.dupont@gmail.com"
    p = row(conn, pid)
    assert p["auto_state"] == "active" and json.loads(p["provenance"])["presend_site"]["result"] == "none"


def test_doute_ou_delai_depasse_pas_d_envoi_puis_sortie_de_file(mail, conn, monkeypatch):
    pid, web = _queue_dupont(conn, monkeypatch)
    mail.enable(start_hour=6, end_hour=22, pause_start=6, pause_end=7)     # plage large : les essais espacés d'une heure restent dans la fenêtre
    monkeypatch.setattr(outbox, "_resolve_bounded", lambda *a, **k: None)              # délai dépassé / erreur réseau
    t = T0
    for i in range(outbox.PRESEND_MAX_TRIES):
        assert mail.run(t, fetch=web)["status"] == "deferred" and mail.smtp.sent == []
        if i < outbox.PRESEND_MAX_TRIES - 1:
            assert row(conn, pid)["auto_state"] == "queued"
            assert mail.run(t + dt.timedelta(minutes=2), fetch=web)["status"] == "idle"     # pas avant PRESEND_RETRY
        t += outbox.PRESEND_RETRY + dt.timedelta(minutes=1)
    p = row(conn, pid)
    assert p["auto_state"] is None and json.loads(p["provenance"])["presend_site"]["doubt"] is True   # sortie de la file
    assert complete.missing(p, True) == ["site"] and mail.tg.texts == []                               # site « à trancher », sans alerte
    # le message « sans site » n'est plus sûr : brouillon effacé, fiche rendue à la rédaction, en neutre
    assert p["draft_message"] is None and p["draft_kind"] is None and complete.angle(p) == "neutre"
    assert mail.run(t, fetch=web)["status"] == "idle"


# ── Message « site inaccessible » : l'URL est rouverte juste avant l'envoi ───────────────────────────────────
DOWN = "dupont-en-panne.fr"
LEGAL_DOWN = ("<html><head><title>Mentions légales</title></head><body><p>BOULANGERIE DUPONT - SIRET 222 222 222 00011 - 5 rue Voltaire 10000 Troyes"
              "</p></body></html>")


def row_by_siret(conn) -> int:
    return db.fetch_one(conn, "SELECT id FROM local_prospects WHERE siret=%s", (DUPONT,))["id"]


def _queue_down(conn, monkeypatch, confirmed: bool = True):
    """Fiche complète au site « en panne » (2 nouveaux essais déjà faits), brouillon « site inaccessible » en file. Le site est confirmé par
    toi (`site_review`) : seul cas où un site injoignable reste « vérifié » (angle refonte) ; sinon l'angle est neutre."""
    web, _search = _setup(conn, monkeypatch)
    sets = {"email": "boulangerie.dupont@gmail.com", "email_kind": "UNCERTAIN", "activity_label": "Boulangeries, pâtisseries", "draft_message": BODY,
            "website_status": "UNREACHABLE", "website_url": f"https://{DOWN}/", "draft_kind": "site_inaccessible",
            "site_review": "CONFIRMED" if confirmed else None}
    db.execute(conn, f"UPDATE local_prospects SET {', '.join(f'{k}=%s' for k in sets)} WHERE siret=%s", (*sets.values(), DUPONT))
    db.execute(conn, """UPDATE local_prospects SET provenance = JSON_SET(COALESCE(provenance, JSON_OBJECT()), '$.unreachable_retry',
                        JSON_OBJECT('tries', 2, 'status', 'UNREACHABLE', 'checked_at', '2026-10-20T10:00:00Z')) WHERE siret=%s""", (DUPONT,))
    conn.commit()
    pid = row_by_siret(conn)
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])
    assert complete.missing(row(conn, pid), True) == []
    return pid, web


def test_site_injoignable_non_confirme_brouillon_rendu_a_la_redaction_en_neutre(mail, conn, monkeypatch):
    pid, web = _queue_down(conn, monkeypatch, confirmed=False)
    mail.enable()
    assert mail.run(T0, fetch=web)["status"] == "idle" and mail.smtp.sent == [] and f"https://{DOWN}/" not in web.calls   # ni envoi ni lecture
    p = row(conn, pid)
    assert p["draft_message"] is None and p["draft_kind"] is None and p["auto_state"] is None and p["contacted_at"] is None
    assert complete.angle(p) == "neutre" and complete.reason(p, True) is None                         # e-mail toujours utilisable


def test_site_toujours_injoignable_le_message_part(mail, conn, monkeypatch):
    pid, web = _queue_down(conn, monkeypatch)                                          # domaine inconnu du faux web : injoignable
    mail.enable()
    r = mail.run(T0, fetch=web)
    assert r["status"] == "sent" and len(mail.smtp.sent) == 1 and f"https://{DOWN}/" in web.calls


def test_site_revenu_rien_n_est_envoye_brouillon_efface_fiche_revue(mail, conn, monkeypatch):
    pid, web = _queue_down(conn, monkeypatch)
    web.add_site(DOWN, "<html><head><title>Boulangerie Dupont Troyes</title></head><body><h1>Boulangerie Dupont</h1>"
                       "<a href='/mentions-legales'>Mentions légales</a></body></html>",
                 legal=LEGAL_DOWN)
    mail.enable()
    r = mail.run(T0, fetch=web)
    p = row(conn, pid)
    assert r["status"] == "found_site" and mail.smtp.sent == [] and mail.tg.texts == []          # rien d'envoyé, aucune alerte
    assert p["website_status"] in ("CONFIRMED", "PROBABLE") and p["draft_message"] is None and p["draft_kind"] is None
    assert p["auto_state"] is None and p["contacted_at"] is None
    assert json.loads(p["provenance"])["presend_site"]["result"] == "site_up"
    assert mail.run(T0 + dt.timedelta(minutes=2), fetch=web)["status"] == "idle"                  # sortie de la file


def test_domaine_qui_repond_mais_n_est_plus_le_sien_retour_en_recherche(mail, conn, monkeypatch):
    pid, web = _queue_down(conn, monkeypatch)
    web.add_site(DOWN, "<html><head><title>Domaine à vendre</title></head><body><p>Ce domaine est à vendre.</p></body></html>")
    mail.enable()
    assert mail.run(T0, fetch=web)["status"] == "found_site" and mail.smtp.sent == []
    p = row(conn, pid)
    assert p["website_status"] in ("NOT_FOUND", "UNCERTAIN") and p["draft_message"] is None and p["auto_state"] is None
    assert complete.site_reason(p) in ("search", "uncertain", "uncertain_user")                  # à chercher par l'enrichissement


def test_delai_depasse_de_notre_cote_report_sans_envoi(mail, conn, monkeypatch):
    from worker.local import errors
    pid, _web = _queue_down(conn, monkeypatch)

    class SlowNet:                                                                     # transport factice : délai dépassé à la lecture
        failures: list = []

        def __call__(self, url):
            self.failures.append((errors.SITE_TIMEOUT, url))
            return None
    mail.enable()
    r = mail.run(T0, fetch=SlowNet())
    p = row(conn, pid)
    assert r["status"] == "deferred" and mail.smtp.sent == [] and p["auto_state"] == "queued"
    assert json.loads(p["provenance"])["presend_site"]["result"] == "retry"


def test_fil_trop_lent_report_et_site_qui_bloque_les_robots(mail, conn, monkeypatch):
    import time as _t
    pid, web = _queue_down(conn, monkeypatch)
    monkeypatch.setattr(outbox, "PRESEND_PROBE_TIMEOUT", 0.05)
    mail.enable()
    assert mail.run(T0, fetch=lambda url: _t.sleep(0.5))["status"] == "deferred" and mail.smtp.sent == []
    verdict, _detail = outbox.presend_unreachable(conn, row(conn, pid), T0, fetch=lambda url: type("R", (), {"status": 403, "text": "", "ok": False})())
    assert verdict == "retry"                                                          # 403 : peut-être joignable, jamais « inaccessible »


# ── Domaine de l'e-mail pro : un vrai site y est hébergé, le message « création » serait faux ─────────────────
CLUB_DOM = "club-dupont-troyes.com"


def test_domaine_de_l_email_avec_un_site_redraft_avant_l_envoi(mail, conn, monkeypatch):
    pid, web = _queue_dupont(conn, monkeypatch, email="contact@" + CLUB_DOM, email_kind="GENERIC_BUSINESS", draft_kind="sans_site")
    web.add_site(CLUB_DOM, html(title="Accueil", h1="Bienvenue", desc="Pains", canonical=False, body="<p>Troyes 10000</p>"))
    mail.enable()
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])
    r = mail.run(T0, fetch=web)
    p = row(conn, pid)
    assert r["status"] == "found_site" and mail.smtp.sent == [] and mail.tg.texts == []
    assert p["draft_message"] is None and p["draft_kind"] is None and p["auto_state"] is None and p["contacted_at"] is None
    assert p["website_status"] in ("CONFIRMED", "PROBABLE") and p["website_url"] == f"https://{CLUB_DOM}/"
    assert json.loads(p["provenance"])["email_domain_site"]["domain"] == CLUB_DOM


def test_domaine_de_l_email_sans_site_le_message_part(mail, conn, monkeypatch):
    pid, web = _queue_dupont(conn, monkeypatch, email="contact@" + CLUB_DOM, email_kind="GENERIC_BUSINESS", draft_kind="sans_site")
    mail.enable()                                                                  # domaine injoignable
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])
    r = mail.run(T0, fetch=web)
    assert r["status"] == "sent" and json.loads(row(conn, pid)["provenance"])["email_domain_site"]["status"] == "NOT_FOUND"


def test_domaine_pro_en_construction_le_message_part(mail, conn, monkeypatch):
    pid, web = _queue_dupont(conn, monkeypatch, email="contact@" + CLUB_DOM, email_kind="GENERIC_BUSINESS", draft_kind="sans_site")
    web.add_site(CLUB_DOM, html(title="Site en construction", h1="Site en construction", desc=None, canonical=False, body="<p>Ce site est en cours de construction.</p>"))
    mail.enable()
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])
    assert mail.run(T0, fetch=web)["status"] == "sent"
