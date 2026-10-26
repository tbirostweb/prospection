"""Nouvelle recherche du site des fiches « sans site » sur un VRAI MySQL : site trouvé grâce aux nouvelles devinettes (nom collé, titre générique,
SIRET dans les mentions légales), contacts AJOUTÉS sans rien effacer, brouillon « sans site » effacé, fiche retirée de la file d'envoi. Une fiche
contactée ou en séquence n'est jamais touchée. Moteurs et sites simulés (aucune requête réelle)."""
from __future__ import annotations

from local_fixtures import html

from worker import db
from worker.local import runner, sitefinder, siterecheck

from .test_local_pipeline import add_campaign, install_api, prospect, web_and_search

DUPONT = "22222222200011"
SITE = "boulangeriedupont.fr"
LEGAL = html(title="Mentions légales", h1="Mentions légales",
             body="<p>BOULANGERIE DUPONT - SIRET 222 222 222 00011 - 5 rue Voltaire 10000 Troyes - 03 25 40 50 60 - contact@boulangeriedupont.fr</p>")


def _setup(conn, monkeypatch, **dupont):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    assert prospect(conn, DUPONT)["website_status"] == "NOT_FOUND"
    sets = {"phone": "0325998877", "draft_message": "Bonjour, je n'ai pas trouvé de site…", "draft_subject": "Votre site", "draft_kind": "sans_site",
            "draft_created_at": "2026-10-20 10:00:00", "auto_state": "queued", "auto_queued_at": "2026-10-20 10:00:00", **dupont}
    db.execute(conn, f"UPDATE local_prospects SET {', '.join(f'{k}=%s' for k in sets)} WHERE siret=%s", (*sets.values(), DUPONT))
    conn.commit()
    # le site existe depuis : domaine au nom collé, accueil au titre générique, aucun lien, SIRET seulement dans /mentions-legales
    web.add_site(SITE, html(title="Accueil", h1="Bienvenue", desc="Pains et viennoiseries", canonical=False, body="<p>Artisan depuis 1990.</p>"), legal=LEGAL)
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [d for d in ds if d == SITE])
    search.queries.clear()
    return web, search


def _ids(conn):
    return [r["id"] for r in siterecheck.candidates(conn, 50)]


def test_site_trouve_contacts_ajoutes_brouillon_efface_file_videe(env, conn, monkeypatch):
    web, search = _setup(conn, monkeypatch)
    stats = siterecheck.run(conn, search, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert stats["found"] == 1 and p["website_status"] == "CONFIRMED" and p["website_url"] == f"https://{SITE}/"
    assert p["phone"] == "0325998877"                                                       # téléphone connu : jamais effacé ni remplacé
    assert p["email"] == "contact@boulangeriedupont.fr"                                     # e-mail du site : ajouté
    assert p["draft_message"] is None and p["draft_subject"] is None and p["draft_kind"] is None and p["draft_created_at"] is None
    assert p["auto_state"] is None and p["auto_queued_at"] is None and p["contacted_at"] is None
    assert '"site_recheck"' in p["provenance"] and p["pipeline_stage"] == "DONE" and p["scored_at"] is not None
    assert not any("DUPONT" in q for q in search.queries)                                   # domaine deviné : aucune requête pour cette fiche
    assert len(search.queries) <= siterecheck.RECHECK_STRATEGIES * stats["rechecked"]       # les autres fiches : recherches bornées
    assert p["id"] not in _ids(conn)                                                       # une seule fois par fiche


def test_rien_de_sur_seule_la_provenance_est_posee(env, conn, monkeypatch):
    web, search = _setup(conn, monkeypatch)
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])            # toujours introuvable
    before = prospect(conn, DUPONT)
    stats = siterecheck.run(conn, search, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert stats["rechecked"] >= 1 and stats["found"] == 0 and '"site_recheck"' in p["provenance"]
    assert p["website_status"] == "NOT_FOUND" and p["draft_message"] == before["draft_message"] and p["auto_state"] == "queued"
    assert p["website_evidence"] == before["website_evidence"] and p["id"] not in _ids(conn)


def test_fiche_contactee_ou_en_sequence_jamais_touchee(env, conn, monkeypatch):
    web, search = _setup(conn, monkeypatch, auto_state="active", contacted_at="2026-10-21 09:00:00", status="CONTACTED")
    assert prospect(conn, DUPONT)["id"] not in _ids(conn)
    siterecheck.run(conn, search, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert p["website_status"] == "NOT_FOUND" and p["draft_message"] is not None and p["auto_state"] == "active" and "site_recheck" not in (p["provenance"] or "")
    assert not any("DUPONT" in q for q in search.queries)


def test_brouillon_redige_d_abord(env, conn, monkeypatch):
    web, search = _setup(conn, monkeypatch)
    ids = [r["id"] for r in siterecheck.candidates(conn, 10)]
    assert ids and ids[0] == prospect(conn, DUPONT)["id"]
