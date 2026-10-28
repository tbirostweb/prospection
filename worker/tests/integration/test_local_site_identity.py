"""Règle d'identité du site sur un VRAI MySQL : rattrapage des fiches déjà retenues (enrich.site_identity) et contrôle juste avant l'envoi
(outbox.presend_identity). Site qui ne passe plus : fiche « à trancher », brouillon effacé, sortie de la file, coordonnées lues sur ce site
retirées, sans alerte ; fiche contactée : provenance seule ; site que tu as confirmé : jamais touché. Sites simulés (aucune requête réelle)."""
from __future__ import annotations

import json

from local_fixtures import FakeWeb, html

from worker import db
from worker.local import enrich, sitefinder

from .test_local_mail import BODY, T0, mail, row  # noqa: F401 — fixture `mail` réutilisée

__all__ = ["mail"]

FUHRMANN = html(title="Fuhrmann Espaces Verts - Paysagiste dans l'Aube (10)", h1="Création et entretien de jardins", canonical=False,
                body="<p>Paysagiste dans l'Aube (10) depuis 10 ans. Interventions autour de Troyes.</p><p>10410 Saint-Parres-aux-Tertres - "
                     "03 25 99 88 77 - contact@fuhrmann-espacesverts.fr</p><footer>© 2024 Fuhrmann Espaces Verts - Tous droits réservés</footer>")
MARTIN_HOME = html(body="<p>Pains et viennoiseries au levain, 12 rue Émile Zola à Troyes.</p>")


def _web() -> FakeWeb:
    return FakeWeb().add_site("www.fuhrmann-espacesverts.fr", FUHRMANN).add_site("www.boulangerie-martin.fr", MARTIN_HOME)


def _add(conn, siret: str, name: str, url: str, trade: str | None = None, **cols) -> int:
    """Fiche « site retenu » (CONFIRMED), brouillon de refonte en file d'envoi, e-mail lu sur ce site."""
    dom = url.split("//")[1].strip("/").removeprefix("www.")
    base = {"siret": siret, "siren": siret[:9], "fingerprint": f"fp{siret}", "company_name": name, "trade_name": trade, "city": "Troyes",
            "postal_code": "10000", "email": f"contact@{dom}", "email_kind": "GENERIC_BUSINESS", "status": "QUALIFIED", "category": "A_CONTACTER",
            "activity_key": "garages", "activity_label": "Garages", "website_status": "CONFIRMED", "website_url": url, "canonical_domain": dom,
            "website_confidence": 0.8, "pipeline_stage": "DONE", "draft_subject": "Votre site", "draft_message": BODY, "draft_kind": "site_ameliorable",
            "auto_state": "queued", "contact_page": f"{url}contact", "contact_form": 1,
            "provenance": json.dumps({"email": {"source": "site", "url": f"{url}contact"}, "places": {"site_checked": "probable", "had_website": True}}),
            **cols}
    db.execute(conn, f"INSERT INTO local_prospects ({', '.join(base)}, auto_queued_at) VALUES ({', '.join(['%s'] * len(base))}, UTC_TIMESTAMP())",
               tuple(base.values()))
    conn.commit()
    return db.fetch_one(conn, "SELECT id FROM local_prospects WHERE siret=%s", (siret,))["id"]


def test_rattrapage_site_d_une_autre_entreprise_retire_fiche_contactee_intacte(env, conn):
    auto10 = _add(conn, "41234567800012", "SARL AUTO 10", "https://www.fuhrmann-espacesverts.fr/")
    martin = _add(conn, "12345678900011", "MARTIN BOULANGERIE SARL", "https://www.boulangerie-martin.fr/", trade="Boulangerie Martin")
    sent = _add(conn, "41234567800020", "SARL AUTO 10", "https://www.fuhrmann-espacesverts.fr/", status="CONTACTED", auto_state="active",
                contacted_at="2026-10-20 10:00:00")
    mine = _add(conn, "41234567800038", "SARL AUTO 10", "https://www.fuhrmann-espacesverts.fr/", site_review="CONFIRMED")
    web = _web()
    out = enrich.site_identity(conn, web, limit=10)
    assert out["done"] == 3 and out["found"] == 1                                 # site confirmé par toi : jamais relu

    p = row(conn, auto10)
    assert p["website_status"] == "NOT_FOUND" and p["website_url"] is None and p["canonical_domain"] is None
    assert p["draft_message"] is None and p["draft_kind"] is None and p["auto_state"] is None and p["contacted_at"] is None
    assert p["email"] is None and p["contact_page"] is None and p["contact_form"] == 0       # coordonnées du paysagiste : retirées
    prov = json.loads(p["provenance"])
    assert prov["site_identity"]["result"] == "other_company" and prov["site_identity"]["version"] == sitefinder.IDENTITY_VERSION
    assert prov["places"]["site_checked"] == "rejected:other_company"
    assert json.loads(p["website_evidence"])["candidates"][0]["domain"] == "fuhrmann-espacesverts.fr"
    assert db.fetch_one(conn, "SELECT 1 AS x FROM local_bad_sites WHERE siret=%s AND domain='fuhrmann-espacesverts.fr'", ("41234567800012",))

    m = row(conn, martin)                                                         # vrai site : rien ne change
    assert m["website_status"] == "CONFIRMED" and m["draft_message"] == BODY and m["auto_state"] == "queued"
    assert json.loads(m["provenance"])["site_identity"]["result"] == "ok"

    s = row(conn, sent)                                                           # déjà contactée : provenance seule
    assert s["website_status"] == "CONFIRMED" and s["website_url"] and s["draft_message"] == BODY and s["email"] and s["auto_state"] == "active"
    assert json.loads(s["provenance"])["site_identity"]["contacted"] is True
    assert row(conn, mine)["website_status"] == "CONFIRMED" and "site_identity" not in json.loads(row(conn, mine)["provenance"])

    assert enrich.site_identity(conn, web, limit=10)["done"] == 0                # idempotent : une fois par version de la règle


def test_rattrapage_page_de_maintenance_redevient_a_trancher_coordonnees_gardees(env, conn):
    page = html(title="Electri'Zon", h1="Site en Maintenance", canonical=False, body="<p>Notre site est en cours de maintenance. Nous serons bientôt de retour.</p>")
    pid = _add(conn, "81234567800017", "ELECTRI'ZON", "http://www.electrizon.fr/")
    enrich.site_identity(conn, FakeWeb().add_site("www.electrizon.fr", page, https=False), limit=10)
    p = row(conn, pid)
    assert p["website_status"] == "UNCERTAIN" and p["draft_message"] is None and p["auto_state"] is None
    assert p["email"] == "contact@electrizon.fr"                                  # c'est bien la sienne : gardée
    assert json.loads(p["provenance"])["site_identity"]["result"] == "maintenance"


def test_rattrapage_site_injoignable_repris_plus_tard_rien_d_autre(env, conn):
    pid = _add(conn, "41234567800012", "SARL AUTO 10", "https://www.fuhrmann-espacesverts.fr/")
    enrich.site_identity(conn, FakeWeb(), limit=10)
    p = row(conn, pid)
    flag = json.loads(p["provenance"])["site_identity"]
    assert p["website_status"] == "CONFIRMED" and p["auto_state"] == "queued" and flag["result"] == "unreachable" and flag["tries"] == 1
    assert enrich.site_identity(conn, FakeWeb(), limit=10)["done"] == 0          # pas avant le lendemain


def test_avant_l_envoi_site_d_une_autre_entreprise_rien_ne_part(mail, conn):
    pid = _add(conn, "41234567800012", "SARL AUTO 10", "https://www.fuhrmann-espacesverts.fr/")
    mail.enable()
    r = mail.run(T0, fetch=_web())
    p = row(conn, pid)
    assert r["status"] == "found_site" and mail.smtp.sent == [] and mail.tg.texts == []            # aucun envoi, aucune alerte
    assert p["website_status"] == "NOT_FOUND" and p["draft_message"] is None and p["auto_state"] is None and p["email"] is None
    assert json.loads(p["provenance"])["presend_identity"]["result"] == "other_company"


def test_avant_l_envoi_vrai_site_le_message_part_site_muet_reporte(mail, conn):
    pid = _add(conn, "12345678900011", "MARTIN BOULANGERIE SARL", "https://www.boulangerie-martin.fr/", trade="Boulangerie Martin")
    mail.enable()
    r = mail.run(T0, fetch=FakeWeb())                                             # site muet : pas d'envoi, nouvel essai plus tard
    assert r["status"] == "deferred" and mail.smtp.sent == [] and row(conn, pid)["auto_state"] == "queued"
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_REMOVE(provenance, '$.presend_site') WHERE id=%s", (pid,))
    conn.commit()
    r = mail.run(T0, fetch=_web())
    p = row(conn, pid)
    assert r["status"] == "sent" and mail.smtp.sent[0]["To"] == "contact@boulangerie-martin.fr" and p["website_status"] == "CONFIRMED"
    assert json.loads(p["provenance"])["presend_identity"]["result"] == "ok"
