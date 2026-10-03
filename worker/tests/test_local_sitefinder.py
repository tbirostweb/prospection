"""websiteConfidence : le point le plus critique du pipeline local. Une mauvaise correspondance entreprise/site est une ERREUR CRITIQUE."""
import pytest

from worker.local import sitefinder as sf
from worker.local.sitefinder import Hit

from local_fixtures import GOOD_JSONLD, LEGAL_MARTIN, MARTIN, FakeSearch, FakeWeb, html


def martin_site(web: FakeWeb, domain="www.boulangerie-martin.fr", **kw):
    home = html(body='<a href="/mentions-legales">Mentions légales</a><a href="/contact">Contact</a>')
    return web.add_site(domain, home, legal=LEGAL_MARTIN, **kw)


def hit(domain, title="Boulangerie Martin - Troyes", content="Boulangerie artisanale à Troyes"):
    return Hit(f"https://{domain}/", title, content)


def test_site_officiel_correctement_trouve_avec_siret_sur_les_mentions_legales():
    web = martin_site(FakeWeb())
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("www.boulangerie-martin.fr")]), web)
    assert res.status == "CONFIRMED" and res.url == "https://www.boulangerie-martin.fr/"
    assert res.confidence >= 0.80
    assert any("SIRET" in e["label"] for e in res.evidence)                          # la preuve la plus forte est citée


def test_jamais_le_premier_resultat_automatiquement():
    """Le premier résultat est le site d'un homonyme (autre ville) ; le bon site est en 2e position."""
    web = martin_site(FakeWeb())
    web.add_site("boulangerie-martin-lyon.fr", html(title="Boulangerie Martin - Lyon", body="<p>8 rue de la République 69002 Lyon</p>"))
    search = FakeSearch(default=[hit("boulangerie-martin-lyon.fr", "Boulangerie Martin - Lyon", "Boulangerie à Lyon"), hit("www.boulangerie-martin.fr")])
    res = sf.resolve(MARTIN, search, web)
    assert res.status == "CONFIRMED" and res.url == "https://www.boulangerie-martin.fr/"
    assert len(search.queries) == 1                          # l'homonyme écarté (faible confiance) ne fait pas continuer la recherche


def test_homonyme_dans_une_autre_ville_seul_n_est_jamais_accepte():
    web = FakeWeb().add_site("boulangerie-martin-lyon.fr", html(title="Boulangerie Martin - Lyon", body="<p>Boulangerie Martin, 8 rue de la République 69002 Lyon, 04 72 00 00 00</p>"))
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("boulangerie-martin-lyon.fr", "Boulangerie Martin Lyon", "Lyon")]), web)
    assert res.status != "CONFIRMED" and res.url is None                 # jamais rattaché au mauvais établissement
    cand = res.candidates[0]
    assert cand["confidence"] < sf.ACCEPT and any("homonyme" in e["label"] for e in cand["evidence"])


def test_mauvais_site_associe_par_erreur_reste_sous_le_seuil():
    """Un site d'une AUTRE entreprise (aucun mot du nom) qui cite Troyes ne doit pas être pris pour le nôtre."""
    web = FakeWeb().add_site("garage-durand.fr", html(title="Garage Durand - Troyes", h1="Garage Durand", body="<p>Garage Durand 5 rue Voltaire 10000 Troyes</p>"))
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("garage-durand.fr", "Garage Durand Troyes", "Boulangerie Martin voisin du garage à Troyes")]), web)
    assert res.status != "CONFIRMED"
    assert all(c["confidence"] <= 0.30 for c in res.candidates)             # nom introuvable dans le titre/en-tête/domaine : plafonné


def test_annuaires_et_reseaux_sociaux_ne_sont_pas_le_site_officiel():
    web = FakeWeb()
    search = FakeSearch(default=[Hit("https://www.pagesjaunes.fr/pros/123", "Boulangerie Martin Troyes - PagesJaunes", "Troyes"),
                                 Hit("https://www.facebook.com/boulangeriemartin", "Boulangerie Martin - Troyes | Facebook", "Troyes"),
                                 Hit("https://www.societe.com/societe/martin-123456789.html", "MARTIN BOULANGERIE", "Troyes")])
    res = sf.resolve(MARTIN, search, web)
    assert res.status == "NOT_FOUND" and res.url is None
    assert res.socials == ["https://www.facebook.com/boulangeriemartin"]      # présence sur les réseaux notée, pas prise pour un site
    assert web.calls == []                                                   # aucun de ces sites n'a même été téléchargé


def test_aucun_resultat_donne_site_non_trouve_et_deux_recherches_au_moins():
    search = FakeSearch()
    res = sf.resolve(MARTIN, search, FakeWeb())
    assert res.status == "NOT_FOUND" and len(search.queries) >= 2
    assert '"Boulangerie Martin" "TROYES"' in search.queries[0]              # nom + ville, jamais le nom seul


def test_confiance_intermediaire_donne_site_incertain_sans_url():
    """Nom + ville sur le site mais ni SIRET ni adresse : plausible, pas assez sûr → UNCERTAIN, l'URL n'est PAS enregistrée."""
    web = FakeWeb().add_site("martin-pain.fr", html(title="Boulangerie Martin", h1="Boulangerie Martin", body="<p>Nos pains à Troyes 10000</p>", desc=None))
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("martin-pain.fr", "Boulangerie Martin", "Troyes")]), web)
    assert res.status == "UNCERTAIN" and res.url is None
    assert sf.UNCERTAIN_MIN <= res.confidence < sf.PROBABLE_MIN


def test_deux_sites_plausibles_est_ambigu():
    web = martin_site(FakeWeb())
    web.add_site("boulangerie-martin.com", html(body='<a href="/mentions-legales">ML</a>'), legal=LEGAL_MARTIN)
    search = FakeSearch(default=[hit("www.boulangerie-martin.fr"), hit("boulangerie-martin.com")])
    res = sf.resolve(MARTIN, search, web)
    assert res.status == "UNCERTAIN" and any("ambigu" in e["label"] for e in res.evidence)


def test_site_injoignable_n_est_jamais_accepte_sur_le_seul_resultat_de_recherche():
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("www.boulangerie-martin.fr")]), FakeWeb())      # aucune page servie
    assert res.status == "NOT_FOUND" and res.url is None and res.candidates[0]["reachable"] is False     # illisible : jamais même « incertain »
    assert res.confidence <= 0.45


def test_donnees_structurees_renforcent_la_confiance():
    home = html(jsonld=GOOD_JSONLD, body="<p>12 rue Emile Zola 10000 Troyes</p>")
    web = FakeWeb().add_site("boulangerie-martin.fr", home)
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("boulangerie-martin.fr")]), web)
    assert res.status == "CONFIRMED" and any("schema.org" in e["label"] for e in res.evidence)


def test_meme_nom_generique_dans_deux_villes_l_adresse_departage():
    """Deux « Salon Coiffure Belle » : seule la ville / l'adresse / le code postal identifie le bon."""
    company = dict(MARTIN, company_name="SALON BELLE", trade_name="Salon Belle Coiffure", siret="99999999900022", siren="999999999",
                   address="3 PLACE DU MARCHE 10000 TROYES")
    web = FakeWeb()
    web.add_site("salon-belle-coiffure.fr", html(title="Salon Belle Coiffure - Nancy", h1="Salon Belle Coiffure", body="<p>Salon Belle Coiffure, 9 rue Stanislas 54000 Nancy</p>"))
    res = sf.resolve(company, FakeSearch(default=[hit("salon-belle-coiffure.fr", "Salon Belle Coiffure Nancy", "Nancy")]), web)
    assert res.status != "CONFIRMED"


@pytest.mark.parametrize("company,expected", [
    (dict(MARTIN, trade_name=None, company_name="SARL LES 3 FRERES"),
     ['"SARL LES 3 FRERES" "TROYES"', '"SARL LES 3 FRERES" "12 Rue Emile Zola" TROYES', '"SARL LES 3 FRERES" "10000"', '"SARL LES 3 FRERES" "123456789"',
      "SARL LES 3 FRERES TROYES site officiel"]),
    (dict(MARTIN, city=None, postal_code=None, address=None), ['"Boulangerie Martin" "123456789"']),
])
def test_requetes_construites_sans_le_nom_seul(company, expected):
    assert sf.build_queries(company) == expected


# ── domaines devinés d'après le nom : aucun moteur de recherche consommé ──────────────────────────────────
def test_domaines_devines_d_apres_le_nom():
    g = sf.guess_domains(MARTIN)
    assert "boulangerie-martin.fr" in g and "boulangeriemartin.fr" in g and len(g) <= sf.GUESS_MAX
    assert sf.guess_domains(dict(company_name="BOULANGERIE PATISSERIE SARL", city="TROYES")) == []     # que des mots d'activité : rien à deviner


def test_site_trouve_par_domaine_devine_sans_aucune_recherche():
    web = martin_site(FakeWeb(), domain="boulangerie-martin.fr")
    search = FakeSearch()
    res = sf.resolve(MARTIN, search, web, dns=lambda ds: [d for d in ds if d == "boulangerie-martin.fr"])
    assert res.status == "CONFIRMED" and res.url == "https://boulangerie-martin.fr/" and search.queries == []
    assert any(e["code"] == "guessed_domain" for e in res.evidence)


def test_domaine_devine_d_un_homonyme_n_est_pas_retenu():
    web = FakeWeb().add_site("boulangerie-martin.fr", html(title="Boulangerie Martin - Lyon", body="<p>8 rue de la République 69002 Lyon</p>"))
    res = sf.resolve(MARTIN, FakeSearch(), web, dns=lambda ds: ["boulangerie-martin.fr"])
    assert res.status != "CONFIRMED" and res.url is None


def test_moteurs_satures_un_site_deja_verifie_est_retenu_sinon_on_ne_conclut_pas():
    def down(_q):
        raise sf.SearchUnavailable("tous les moteurs sont en cooldown")
    web = martin_site(FakeWeb(), domain="boulangerie-martin.fr")
    res = sf.resolve(MARTIN, down, web, dns=lambda ds: ["boulangerie-martin.fr"])
    assert res.status in ("CONFIRMED", "PROBABLE") and res.url
    with pytest.raises(sf.SearchUnavailable):
        sf.resolve(MARTIN, down, FakeWeb(), dns=lambda ds: [])                  # rien de vérifié : jamais « site non trouvé »


# ── Sites gratuits (Wix…) : de VRAIS sites officiels, autrefois écartés comme des annuaires ────────────────
def test_site_wix_gratuit_est_retenu_comme_site_officiel():
    web = FakeWeb()
    home = html(title="Boulangerie Martin - Troyes", body='<a href="https://martin123.wixsite.com/boulangerie-martin/mentions-legales">Mentions légales</a>')
    web.pages["https://martin123.wixsite.com/boulangerie-martin/"] = (200, home)
    web.pages["https://martin123.wixsite.com/boulangerie-martin/mentions-legales"] = (200, LEGAL_MARTIN)
    search = FakeSearch(default=[Hit("https://martin123.wixsite.com/boulangerie-martin/accueil", "Boulangerie Martin - Troyes", "Boulangerie artisanale à Troyes")])
    res = sf.resolve(MARTIN, search, web)
    assert res.status in ("CONFIRMED", "PROBABLE") and res.url == "https://martin123.wixsite.com/boulangerie-martin/"
    assert res.canonical_domain == "martin123.wixsite.com/boulangerie-martin"                          # deux sites Wix d'un même compte restent distincts


def test_racine_d_un_site_wix_est_dans_le_chemin():
    assert sf._origin("https://compte.wixsite.com/salon-lea/contact") == "https://compte.wixsite.com/salon-lea/"
    assert sf._origin("https://www.salon-lea.fr/contact") == "https://www.salon-lea.fr/"
    assert sf.canonical_domain("https://compte.wixsite.com/") == "compte.wixsite.com"


# ── moins de requêtes : stratégies productives d'abord, aucune requête en double, devinettes gratuites ─────────
FULL = dict(MARTIN, phone="03 25 12 34 56", activity_label="Fabrication de pain, viennoiseries")


def test_strategies_les_plus_productives_d_abord():
    names = [n for n, _q in sf.build_strategies(FULL)]
    assert names == ["name_city", "legal_city", "name_phone", "name_address", "name_activity", "name_postal", "name_siren", "name_official"]


def test_nom_plus_activite_omis_quand_l_activite_est_deja_dans_le_nom():
    names = dict(sf.build_strategies(dict(MARTIN, activity_label="Boulangerie")))
    assert "name_activity" not in names and "name_official" in names
    assert "name_activity" in dict(sf.build_strategies(FULL))


def test_aucune_requete_en_double_aux_guillemets_et_accents_pres():
    queries = sf.build_queries(dict(FULL, company_name="Boulangerie Martin", trade_name="BOULANGERIE MARTIN"))
    keys = [" ".join(sf.fold(q.replace('"', " ")).split()) for q in queries]
    assert len(keys) == len(set(keys)) and not any(n == "legal_city" for n, _ in sf.build_strategies(dict(FULL, company_name="Boulangerie Martin")))


def test_absence_toujours_couverte_par_le_meme_nombre_de_strategies():
    search = FakeSearch()
    res = sf.resolve(FULL, search, FakeWeb())
    assert res.status == "NOT_FOUND" and len(search.queries) == len(res.strategies_tried) == sf.STRATEGY_BUDGET == 6
    assert len(set(search.queries)) == len(search.queries)


def test_cinq_domaines_devines_lus_sans_aucune_recherche():
    fifth = sf.guess_domains(MARTIN)[4]                                                  # 5e domaine deviné existant : autrefois jamais lu
    web = martin_site(FakeWeb(), domain=fifth)
    search = FakeSearch()
    res = sf.resolve(MARTIN, search, web, dns=lambda ds: list(ds))
    assert sf.GUESS_FETCH == 5
    assert res.status == "CONFIRMED" and res.url == f"https://{fifth}/" and search.queries == []
    assert f"https://{sf.guess_domains(MARTIN)[5]}/" not in web.calls                      # jamais plus de 5 domaines devinés lus


def test_domaines_devines_sans_rapport_ne_prennent_pas_la_place_des_resultats_de_recherche():
    web = FakeWeb()
    for g in sf.guess_domains(MARTIN)[:sf.GUESS_FETCH]:                                  # domaines parqués : aucun rapport avec l'entreprise
        web.add_site(g, html(title="Domaine à vendre", h1="Ce domaine est à vendre", desc=None, body="<p>Contactez le registraire</p>"))
    web = martin_site(web, domain="www.martin-troyes-pains.fr")
    web.add_site("boulangerie-martin-lyon.fr", html(title="Boulangerie Martin - Lyon", body="<p>8 rue de la République 69002 Lyon</p>"))
    search = FakeSearch(default=[hit("boulangerie-martin-lyon.fr", "Boulangerie Martin - Lyon", "Boulangerie à Lyon"), hit("www.martin-troyes-pains.fr")])
    res = sf.resolve(MARTIN, search, web, dns=lambda ds: list(ds))
    assert res.status == "CONFIRMED" and res.url == "https://www.martin-troyes-pains.fr/" and len(search.queries) == 1
    assert {"boulangerie-martin-lyon.fr", "martin-troyes-pains.fr"} <= {c["domain"] for c in res.candidates}   # les 2 résultats de recherche ont été lus
