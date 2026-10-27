"""Vérification du site JUSTE AVANT un message « sans site » (outbox.presend_site) sur un VRAI MySQL : site retrouvé par une devinette de domaine
→ rien n'est envoyé, le site est enregistré comme par siterecheck (contacts ajoutés), le brouillon effacé, la fiche sortie de la file, aucune
alerte ; rien trouvé → le message part ; doute ou délai dépassé → pas d'envoi, nouvel essai plus tard, puis sortie de la file. Sites, moteurs,
DNS et SMTP simulés (aucune requête réelle)."""
from __future__ import annotations

import datetime as dt
import json

from worker import db
from worker.local import complete, outbox, sitefinder

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
    mail.enable()
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
    assert mail.run(t, fetch=web)["status"] == "idle"
