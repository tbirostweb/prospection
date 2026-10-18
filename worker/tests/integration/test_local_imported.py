"""Fiches importées (CSV) sur un vrai MySQL : site déclaré vérifié sans moteur, fiche prête, « à vérifier », site récent, seuil appris."""
from __future__ import annotations

import json

from worker import db
from worker.local import imported, today

from local_fixtures import LEGAL_MARTIN, FakeWeb, html


def _imported(conn, siret: str, **over) -> int:
    row = {"siret": siret, "siren": siret[:9], "fingerprint": "csv-" + siret, "company_name": "MARTIN BOULANGERIE SARL", "trade_name": "Boulangerie Martin",
           "activity_key": "boulangeries", "activity_label": "Boulangerie", "city": "TROYES", "postal_code": "10000", "address": "12 RUE EMILE ZOLA 10000 TROYES",
           "status": "DISCOVERED", "pipeline_stage": "IMPORTED", "provenance": json.dumps({"import_csv": {"source": "import CSV", "import_id": 1}}), **over}
    return db.execute(conn, f"INSERT INTO local_prospects ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))


def _get(conn, pid: int) -> dict:
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))


def test_site_declare_confirme_fiche_prete_sans_moteur(env, conn):
    web = FakeWeb().add_site("www.boulangerie-martin.fr", html(body='<a href="/mentions-legales">Mentions légales</a>'), legal=LEGAL_MARTIN)
    pid = _imported(conn, "12345678900011", siren="123456789", website_url="https://www.boulangerie-martin.fr/", website_status="UNCERTAIN",
                    email="contact@boulangerie-martin.fr")
    conn.commit()
    stats = imported.run(conn, fetch=web)
    p = _get(conn, pid)
    assert stats["processed"] == 1 and p["pipeline_stage"] == "DONE" and p["deep_enriched_at"] is not None
    assert p["website_status"] == "CONFIRMED" and p["site_review"] is None and p["site_year"] == 2019
    assert p["email"] == "contact@boulangerie-martin.fr"                                        # coordonnée du fichier jamais remplacée
    assert p["phone"] == "0325123456"                                                           # complétée depuis le site (manquante)
    ready = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM local_prospects p WHERE p.id=%s AND {today.READY}", (pid,))["n"]
    assert ready == 1


def test_fiche_importee_sans_site_prete_avec_un_contact(env, conn):
    pid = _imported(conn, "22222222200011", email="bonjour@exemple-plombier.fr")
    conn.commit()
    imported.run(conn, fetch=FakeWeb())
    p = _get(conn, pid)
    assert p["pipeline_stage"] == "DONE" and p["website_status"] is None                        # jamais « pas de site » sans vraie recherche
    assert db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM local_prospects p WHERE p.id=%s AND {today.READY}", (pid,))["n"] == 1


def test_site_douteux_a_verifier_par_toi(env, conn):
    web = FakeWeb().add_site("www.autre-chose.fr", html(title="Garage Dupont - Lyon", h1="Garage Dupont", desc="Mécanique à Lyon"))
    pid = _imported(conn, "33333333300011", website_url="https://www.autre-chose.fr/", website_status="UNCERTAIN", phone="0325000000")
    conn.commit()
    imported.run(conn, fetch=web)
    p = _get(conn, pid)
    assert p["website_status"] == "UNCERTAIN" and p["site_review"] == "PENDING" and p["pipeline_stage"] == "DONE"


def test_site_recent_ecarte_meme_avec_un_email(env, conn):
    home = html(body='<a href="/mentions-legales">Mentions légales</a>').replace("© 2019", "© 2025")
    web = FakeWeb().add_site("www.boulangerie-martin.fr", home, legal=LEGAL_MARTIN.replace("© 2019", "© 2025"))
    pid = _imported(conn, "12345678900011", siren="123456789", website_url="https://www.boulangerie-martin.fr/", website_status="UNCERTAIN",
                    email="contact@boulangerie-martin.fr")
    conn.commit()
    imported.run(conn, fetch=web)
    p = _get(conn, pid)
    assert p["site_year"] == 2025 and p["category"] == "IGNORER" and p["prospect_score"] == 0


def test_decision_deja_prise_n_est_pas_reverifiee(env, conn):
    web = FakeWeb().add_site("www.autre-chose.fr", html(title="Garage Dupont - Lyon", h1="Garage Dupont", desc="Mécanique à Lyon"))
    pid = _imported(conn, "44444444400011", website_url="https://www.autre-chose.fr/", website_status="CONFIRMED", site_review="CONFIRMED", phone="0325000000")
    conn.commit()
    imported.run(conn, fetch=web)
    assert _get(conn, pid)["website_status"] == "CONFIRMED"                                     # ta décision prime sur le calcul


def test_seuil_appris_de_tes_decisions(env, conn):
    assert imported.learn_threshold(conn, 0.80)[0] == 0.80                                      # pas assez de décisions : seuil par défaut
    for conf in (0.78, 0.74, 0.72, 0.70, 0.69, 0.66):
        db.execute(conn, "INSERT INTO local_site_reviews (prospect_id, url, verdict, confidence) VALUES (NULL, 'https://x.fr/', 'CONFIRMED', %s)", (conf,))
    for conf in (0.50, 0.40):
        db.execute(conn, "INSERT INTO local_site_reviews (prospect_id, url, verdict, confidence) VALUES (NULL, 'https://y.fr/', 'REJECTED', %s)", (conf,))
    conn.commit()
    seuil, why = imported.learn_threshold(conn, 0.80)
    assert seuil == 0.66 and "appris" in why
    db.execute(conn, "INSERT INTO local_site_reviews (prospect_id, url, verdict, confidence) VALUES (NULL, 'https://z.fr/', 'REJECTED', 0.75)")
    conn.commit()
    assert imported.learn_threshold(conn, 0.80)[0] == 0.80                                      # un site rejeté à 0,75 : on reste prudent
