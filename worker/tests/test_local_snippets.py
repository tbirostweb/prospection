"""Coordonnées lues dans les extraits de recherche : attribution stricte (nom + lieu), zone téléphonique, pas de numéro surtaxé, pas d'adresse d'annuaire."""
from worker.local import snippets
from worker.local.sitefinder import Hit

DUPONT = dict(company_name="BOULANGERIE DUPONT", trade_name=None, city="TROYES", postal_code="10000", department="10")


def pj(content, title="Boulangerie Dupont - Troyes", url="https://www.pagesjaunes.fr/pros/123"):
    return Hit(url, title, content)


def test_telephone_et_email_attribues_depuis_un_extrait_d_annuaire():
    c = snippets.extract(DUPONT, [pj("Boulangerie Dupont, 5 rue Voltaire 10000 Troyes. Tél : 03 25 00 11 22 — boulangerie.dupont@gmail.com")])
    assert c["phone"] == "0325001122" and c["email"] == "boulangerie.dupont@gmail.com" and c["email_kind"] == "UNCERTAIN"
    assert c["evidence"][0]["via"] == "search_snippet" and "pagesjaunes.fr" in c["evidence"][0]["source"]
    assert c["contact_confidence"] <= snippets.MAX_CONF and c["phone_confidence"] < 0.6          # jamais assez pour « Très bon »


def test_extrait_sans_le_nom_ou_sans_le_lieu_est_ignore():
    assert snippets.extract(DUPONT, [pj("Boucherie Martin 10000 Troyes 03 25 99 88 77", title="Boucherie Martin")])["phone"] is None   # autre entreprise
    assert snippets.extract(DUPONT, [pj("Boulangerie Dupont à Lyon 03 25 00 11 22", title="Boulangerie Dupont")])["phone"] is None     # pas notre ville


def test_zone_telephonique_et_numeros_surtaxes():
    assert snippets.phone_plausible("0325001122", "10") and snippets.phone_plausible("0612345678", "10") and snippets.phone_plausible("0912345678", "10")
    assert not snippets.phone_plausible("0472001122", "10")          # 04 = Sud-Est, pas l'Aube
    assert not snippets.phone_plausible("0899123456", "10")          # numéro surtaxé de mise en relation d'annuaire
    assert snippets.phone_plausible("0472001122", None)              # département inconnu : on ne rejette pas sur la zone
    c = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 10000 : 08 99 12 34 56 (mise en relation)")])
    assert c["phone"] is None


def test_numeros_contradictoires_le_plus_cite_gagne_egalite_aucun():
    tie = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 03 25 00 11 22"), pj("Boulangerie Dupont Troyes 03 25 99 99 98", url="https://www.118712.fr/x")])
    assert tie["phone"] is None
    maj = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 03 25 00 11 22"), pj("Boulangerie Dupont Troyes 03 25 00 11 22", url="https://www.118712.fr/x"),
                                    pj("Boulangerie Dupont Troyes 03 25 99 99 98", url="https://annuaire.fr/y")])
    assert maj["phone"] == "0325001122" and maj["evidence"][0]["conflicts"] == 1 and set(maj["evidence"][0]["sources"]) == {"pagesjaunes.fr", "118712.fr"}


def test_jamais_l_adresse_de_l_annuaire_ni_une_adresse_sans_rapport():
    c = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes — contact@pagesjaunes.fr — commercial@autre-societe.fr — info@boulangerie-dupont.fr")])
    assert c["email"] == "info@boulangerie-dupont.fr" and c["email_kind"] == "GENERIC_BUSINESS"


def test_requete_dediee_aux_coordonnees():
    assert snippets.contact_query(DUPONT) == '"BOULANGERIE DUPONT" TROYES téléphone'
    assert snippets.contact_query({"company_name": "X"}) is None


def test_email_meilleur_choisi_parmi_plusieurs():
    hits = [pj("Boulangerie Dupont Troyes 10000 : dupont.boulangerie@gmail.com"),
            pj("Boulangerie Dupont Troyes 10000 : jean@boulangerie-dupont.fr", url="https://www.facebook.com/x"),
            pj("Boulangerie Dupont Troyes 10000 : contact@boulangerie-dupont.fr", url="https://www.118712.fr/x")]
    c = snippets.extract(DUPONT, hits)
    assert c["email"] == "contact@boulangerie-dupont.fr" and c["email_kind"] == "GENERIC_BUSINESS"
    assert snippets.extract(DUPONT, hits[:2])["email"] == "jean@boulangerie-dupont.fr"          # nominatif pro avant freemail
    assert snippets.extract(DUPONT, hits[:1])["email"] == "dupont.boulangerie@gmail.com"
    assert snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 10000 : contact@pagesjaunes.fr")])["email"] is None   # adresse de l'annuaire
    assert snippets.extract(DUPONT, [pj("Boulangerie Dupont : x@gmail.com", title="Boulangerie Dupont")])["email"] is None                           # pas de ville : non attribué


def test_requetes_email_plafonnees():
    qs = snippets.email_queries(DUPONT, 4)
    assert len(qs) == 4 and qs[0] == snippets.email_query(DUPONT) and '"@" 10000' in qs[2]
    assert len(snippets.email_queries(DUPONT, 2)) == 2 and snippets.email_queries({"company_name": "X"}) == []


def test_email_search_s_arrete_au_premier_email_et_delai_court_si_rien(monkeypatch):
    import json
    from worker.local import runner, store
    saved = {}
    monkeypatch.setattr(store, "set_provenance", lambda conn, pid, key, val: saved.update(val))
    monkeypatch.setattr(runner, "EMAIL_QUERIES_PER_PROSPECT", 3)
    calls = []

    def search(q):
        calls.append(q)
        return [pj("Boulangerie Dupont Troyes 10000 contact@boulangerie-dupont.fr")] if len(calls) == 2 else []
    found = runner.email_search(None, 1, DUPONT, search)
    assert found["email"] == "contact@boulangerie-dupont.fr" and len(calls) == 2
    assert saved["found"] is True and saved["queries"] == calls and saved["source"]
    calls.clear()
    assert runner.email_search(None, 1, DUPONT, lambda q: calls.append(q) or []) ["email"] is None and len(calls) == 3 and saved["found"] is False
    import time
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 15 * 86400))
    miss = {"provenance": json.dumps({"email_search": {"checked_at": old, "found": False}})}
    hit = {"provenance": json.dumps({"email_search": {"checked_at": old, "found": True}})}
    assert runner.email_recently_searched(miss) is False and runner.email_recently_searched(hit) is True
