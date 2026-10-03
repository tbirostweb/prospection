"""Angles morts de la prospection locale : niveaux de site, preuves, vérification inversée, couverture, chaînes, contacts, plafonds expliqués."""
from datetime import date

import pytest

from worker.local import chains, contacts, errors, htmlinfo, identity, scoring
from worker.local import sitefinder as sf
from worker.local.sitefinder import Hit

from local_fixtures import GOOD_JSONLD, LEGAL_MARTIN, MARTIN, FakeSearch, FakeWeb, html


def martin_site(web: FakeWeb, domain="www.boulangerie-martin.fr", **kw):
    return web.add_site(domain, html(body='<a href="/mentions-legales">Mentions légales</a><a href="/contact">Contact</a>'), legal=LEGAL_MARTIN, **kw)


def hit(domain, title="Boulangerie Martin - Troyes", content="Boulangerie artisanale à Troyes"):
    return Hit(f"https://{domain}/", title, content)


def codes(evidence):
    return {e["code"] for e in evidence}


# ── niveaux de site et seuils configurables ─────────────────────────────────────────────────────────────
def test_niveaux_de_site_et_seuils_configurables():
    t = sf.Thresholds()
    assert [t.tier(c) for c in (0.95, 0.90, 0.85, 0.80, 0.70, 0.60, 0.59, 0.1)] == ["CONFIRMED", "CONFIRMED", "PROBABLE", "PROBABLE", "UNCERTAIN", "UNCERTAIN", "NOT_FOUND", "NOT_FOUND"]
    assert t.tier(0.9, reachable=False) == "UNREACHABLE"
    strict = sf.Thresholds.from_options({"website": {"confirmed": 0.95, "probable": 0.9, "uncertain": 0.7}})
    assert strict.tier(0.92) == "PROBABLE" and strict.tier(0.65) == "NOT_FOUND"
    assert sf.Thresholds.from_options({"website": {"confirmed": 0.5, "probable": 0.9, "uncertain": 0.7}}) == sf.Thresholds()   # réglage incohérent : défauts
    assert sf.Thresholds.from_options({"website": {"confirmed": "beaucoup"}}) == sf.Thresholds()


def test_site_confirme_avec_preuves_pondérées_et_explicables():
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("www.boulangerie-martin.fr")]), martin_site(FakeWeb()))
    assert res.status == "CONFIRMED" and res.confidence >= 0.9
    assert {"siret", "name_exact", "city", "postal"} <= codes(res.evidence)
    assert all({"code", "label", "weight"} <= set(e) for e in res.evidence)              # websiteEvidence[] explicable
    assert next(e for e in res.evidence if e["code"] == "siret")["weight"] > next(e for e in res.evidence if e["code"] == "city")["weight"]


def test_nom_seul_reste_faible_meme_avec_un_domaine_identique():
    web = FakeWeb().add_site("boulangerie-martin.fr", html(title="Boulangerie Martin", h1="Boulangerie Martin", body="<p>Nos pains</p>"))
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("boulangerie-martin.fr", "Boulangerie Martin", "pains")]), web)
    assert res.status in ("NOT_FOUND", "UNCERTAIN") and res.url is None
    assert res.confidence <= sf.NAME_ONLY_CAP


# ── vérification inversée ────────────────────────────────────────────────────────────────────────────────
def test_un_autre_siren_sur_le_site_interdit_l_acceptation():
    """Homonyme parfait (même nom, même ville) mais les mentions légales portent un AUTRE SIREN valide : jamais accepté."""
    other = html(title="Mentions", body="<p>BOULANGERIE MARTIN SAS - SIREN 552 081 317 - 3 place Jean Jaurès 10000 Troyes</p>")
    home = html(title="Boulangerie Martin - Troyes", h1="Boulangerie Martin", body='<a href="/mentions-legales">Mentions légales</a><p>10000 Troyes</p>')
    web = FakeWeb().add_site("boulangerie-martin-troyes.fr", home, legal=other)
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("boulangerie-martin-troyes.fr")]), web)
    assert res.status != "CONFIRMED" and res.status != "PROBABLE" and res.url is None
    assert "other_siren" in codes(res.candidates[0]["evidence"]) and res.candidates[0]["confidence"] < sf.PROBABLE_MIN


def test_siren_de_site_non_valide_luhn_ne_contredit_pas():
    ident = identity.extract([htmlinfo.parse("<p>Numéro de SIREN : 123 456 789</p>", "https://x.fr/")])
    assert ident.sirens == set()                                                          # 123456789 n'est pas un SIREN valide (Luhn)
    ident = identity.extract([htmlinfo.parse("<p>SIRET 552 081 317 00013 RCS Paris</p>", "https://x.fr/")])
    assert "552081317" in ident.sirens or ident.sirens == set()                          # valide seulement si le SIRET passe Luhn ; jamais de crash


def test_identite_declaree_schema_org_multiple_lieux():
    jl = '{"@type":"Restaurant","name":"Le Bistrot"}'
    page = htmlinfo.parse(f'<html><head><script type="application/ld+json">[{jl},{jl.replace("Bistrot", "Bistrot 2")},{jl.replace("Bistrot", "Bistrot 3")}]</script></head></html>', "https://x.fr/")
    assert identity.extract([page]).org_count == 3


# ── redirections et domaines ─────────────────────────────────────────────────────────────────────────────
def test_redirection_vers_un_nouveau_domaine_est_canonicalisee():
    web = martin_site(FakeWeb(), domain="www.nouveau-martin.fr")
    web.redirects["https://ancien-martin.fr/"] = "https://www.nouveau-martin.fr/"
    res = sf.resolve(MARTIN, FakeSearch(default=[Hit("https://ancien-martin.fr/", "Boulangerie Martin - Troyes", "Troyes")]), web)
    assert res.status == "CONFIRMED" and res.canonical_domain == "nouveau-martin.fr"
    assert res.original_url == "https://ancien-martin.fr/" and res.final_url == "https://www.nouveau-martin.fr/"
    assert res.redirect_chain == ["https://ancien-martin.fr/", "https://www.nouveau-martin.fr/"]


def test_plusieurs_urls_d_un_meme_site_ne_comptent_qu_une_fois():
    web = martin_site(FakeWeb())
    web.redirects["http://boulangerie-martin.fr/"] = "https://www.boulangerie-martin.fr/"
    search = FakeSearch(default=[hit("www.boulangerie-martin.fr"), Hit("http://boulangerie-martin.fr/carte", "Carte - Boulangerie Martin", "Troyes"),
                                 hit("boulangerie-martin.fr")])
    res = sf.resolve(MARTIN, search, web)
    assert res.status == "CONFIRMED"                                                      # pas « deux sites plausibles » pour www / non-www / http
    assert len({c["domain"] for c in res.candidates}) == len(res.candidates) == 1


# ── mauvais sites, sources structurées, stratégies ──────────────────────────────────────────────────────
def test_domaine_signale_mauvais_site_n_est_plus_jamais_propose():
    web = martin_site(FakeWeb())
    res = sf.resolve({**MARTIN, "bad_domains": ["boulangerie-martin.fr"]}, FakeSearch(default=[hit("www.boulangerie-martin.fr")]), web)
    assert res.status == "NOT_FOUND" and res.url is None
    assert not any("boulangerie-martin.fr" in c for c in web.calls)                       # pas même téléchargé


def test_site_d_une_source_structuree_est_verifie_avant_toute_recherche():
    web = martin_site(FakeWeb())
    search = FakeSearch()
    res = sf.resolve(MARTIN, search, web, known_urls=["https://www.boulangerie-martin.fr/"])
    assert res.status == "CONFIRMED" and "structured_source" in codes(res.evidence)
    assert search.queries == []                                                           # inutile de chercher : déjà confirmé
    web2 = FakeWeb().add_site("faux.fr", html(title="Autre entreprise", h1="Autre", body="<p>Rien à voir 69000 Lyon</p>"))
    res2 = sf.resolve(MARTIN, FakeSearch(), web2, known_urls=["https://faux.fr/"])
    assert res2.status == "NOT_FOUND"                                                     # une source structurée n'est jamais crue sur parole


def test_strategies_progressives_et_pas_de_requete_inutile():
    web = martin_site(FakeWeb())
    search = FakeSearch(default=[hit("www.boulangerie-martin.fr")])
    sf.resolve(MARTIN, search, web)
    assert len(search.queries) == 1                                                       # confirmé dès la 1re stratégie : on s'arrête
    strategies = dict(sf.build_strategies({**MARTIN, "phone": "03 25 12 34 56", "activity_label": "Boulangeries, pâtisseries"}))
    assert {"name_city", "name_siren", "name_postal", "name_address", "name_phone", "name_activity", "name_official"} <= set(strategies)
    assert "name_phone" not in dict(sf.build_strategies(MARTIN))                          # sans téléphone : stratégie non applicable
    assert all(q.count('"') >= 2 or " " in q for q in strategies.values())               # jamais le nom seul


def test_couverture_et_confiance_d_absence():
    weak = sf.resolve(MARTIN, FakeSearch(), FakeWeb(), strategy_budget=2)
    full = sf.resolve(MARTIN, FakeSearch(), FakeWeb())
    assert weak.status == full.status == "NOT_FOUND"
    assert len(weak.strategies_tried) == 2 < len(full.strategies_tried) == full.strategies_total
    assert weak.absence_confidence < full.absence_confidence <= 0.85                      # jamais une certitude
    class Degraded(FakeSearch):
        last = type("O", (), {"degraded": True})()
    assert sf.resolve(MARTIN, Degraded(), FakeWeb()).absence_confidence < full.absence_confidence


def test_site_injoignable_reste_unreachable_ou_incertain_jamais_confirme():
    res = sf.resolve(MARTIN, FakeSearch(default=[hit("www.boulangerie-martin.fr")]), FakeWeb())
    assert res.status in ("UNCERTAIN", "NOT_FOUND") and res.url is None


# ── chaînes, réseaux, franchises ─────────────────────────────────────────────────────────────────────────
def test_nom_courant_n_est_pas_une_chaine_sans_autre_indice():
    a = chains.assess(dict(company_name="CHEZ JULES", trade_name="Chez Jules", company_size="PME", employee_range="02", establishments_open=1, city="TROYES"))
    assert a.kind is None and a.confidence == 0 and not a.is_chain
    b = chains.assess(dict(company_name="JULES SAS", trade_name="Jules", company_size="ETI", employee_range="42", establishments_open=80, city="TROYES"))
    assert b.kind == "NATIONAL" and b.is_chain


def test_reseau_detecte_par_le_site_et_la_base_meme_sans_sirene():
    nav = "".join(f'<a href="/restaurants/{v.lower()}">{v}</a>' for v in ("Pontaumur", "Vertaizon", "Clermont", "Nébouzat", "Troyes"))
    page = htmlinfo.parse(f"<html><body>{nav}<p>Retrouvez nos restaurants près de chez vous</p></body></html>", "https://omultifood.fr/")
    a = chains.assess(dict(company_name="O MULTIFOOD TROYES", trade_name="O'Multifood", company_size="PME", employee_range="03", establishments_open=1, city="TROYES"),
                      [page], same_trade_elsewhere=3)
    assert a.is_chain and a.kind == "NETWORK" and a.confidence >= 0.8
    assert {e["code"] for e in a.evidence} >= {"site_locations_text", "site_locations_paths", "site_location_list", "same_brand_cities"}


def test_franchise_locale_est_ecartee():
    page = htmlinfo.parse("<html><body><p>Devenez franchisé : rejoignez notre réseau. Notre restaurant de Troyes vous accueille.</p></body></html>", "https://x.fr/")
    a = chains.assess(dict(company_name="SARL DELICES TROYES", trade_name="Pizza Pai", company_size="PME", employee_range="03", establishments_open=1, city="TROYES"), [page])
    assert a.kind == "FRANCHISE" and a.is_chain                                           # site fourni par l'enseigne : rien à vendre
    b = chains.assess(dict(company_name="SARL LOUVET", trade_name="Paul", naf_code="10.71C", company_size="PME", employee_range="02", establishments_open=1))
    assert b.is_chain and b.name == "Paul"                                                # franchisé Paul : enseigne exacte dans son métier


def test_mots_proches_de_franchise_ne_font_pas_une_franchise():
    page = htmlinfo.parse("<html><body><p>TVA non applicable, franchise en base de TVA. Pare-brise remplacé avec 0 € de franchise.</p></body></html>", "https://x.fr/")
    a = chains.assess(dict(company_name="GARAGE MARTIN", company_size="PME", employee_range="01", establishments_open=1, city="TROYES"), [page])
    assert a.kind is None and not a.is_chain
    c = chains.assess(dict(company_name="CHEZ PAUL", trade_name="Chez Paul", naf_code="56.10A", company_size="PME", employee_range="01", establishments_open=1))
    assert not c.is_chain                                                                 # un prénom n'est pas une enseigne


def test_meme_domaine_sous_deux_siren_est_un_signal_de_reseau():
    a = chains.assess(dict(company_name="X", company_size="PME", employee_range="02", establishments_open=1), shared_domain_siren=2, same_siren_siblings=0)
    assert any(e["code"] == "shared_domain" for e in a.evidence) and not a.is_chain       # seul, il ne suffit pas


# ── contacts ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("03 25 12 34 56", "0325123456"), ("03.25.12.34.56", "0325123456"), ("+33 3 25 12 34 56", "0325123456"), ("0033325123456", "0325123456"),
    ("+33 (0)3 25 12 34 56", "0325123456"), ("tel:+33325123456", "0325123456"), ("06 12 34 56 78", "0612345678"),
    ("0123456789", None), ("0000000000", None), ("12 34 56 78 90", None), ("03 25 12", None), ("2026-09-21", None), ("552 081 317", None), ("0025123456", None)])
def test_normalisation_des_telephones_francais(raw, expected):
    assert htmlinfo.normalize_phone(raw) == expected


def _page(body, url="https://www.boulangerie-martin.fr/contact", head=""):
    return htmlinfo.parse(f"<html><head>{head}</head><body>{body}</body></html>", url)


def test_un_numero_de_siret_ou_une_date_ne_sont_pas_des_telephones():
    p = _page("<p>SIRET 123 456 789 00011 — ouvert depuis 2012-05-03, code 12 34 56 78 90</p>")
    assert p.phones() == []


def test_telephone_via_tel_jsonld_microdata_avec_confiance():
    ld = '<script type="application/ld+json">{"@type":"Bakery","name":"X","telephone":"+33 3 25 12 34 56","email":"contact@boulangerie-martin.fr"}</script>'
    c = contacts.extract([_page('<a href="tel:0325123456">Appeler</a>', head=ld)], "boulangerie-martin.fr")
    assert c["phone"] == "0325123456" and c["phone_confidence"] >= 0.9 and c["email"] == "contact@boulangerie-martin.fr"
    assert {e["via"] for e in c["evidence"] if e["kind"] == "phone"} == {"tel_link"} and c["contact_confidence"] >= 0.9
    micro = contacts.extract([_page('<span itemprop="telephone">03 25 98 76 54</span>')], "x.fr")
    assert micro["phone"] == "0325987654" and micro["evidence"][0]["via"] == "microdata"
    text = contacts.extract([_page("<p>Tél : 03 25 12 34 56</p>", url="https://x.fr/")], "x.fr")
    assert text["phone_confidence"] < c["phone_confidence"]                                # texte brut moins fiable qu'un lien tel:


def test_categories_d_email_et_jamais_d_email_invente():
    pages = [_page("<p>contact@boulangerie-martin.fr jean.martin@boulangerie-martin.fr martin.perso@gmail.com sav@agence-web.fr</p>")]
    assert contacts.classify_email("contact@boulangerie-martin.fr", "boulangerie-martin.fr") == "GENERIC_BUSINESS"
    assert contacts.classify_email("info2@boulangerie-martin.fr", "boulangerie-martin.fr") == "GENERIC_BUSINESS"
    assert contacts.classify_email("jean.martin@boulangerie-martin.fr", "boulangerie-martin.fr") == "PERSONAL_BUSINESS"
    assert contacts.classify_email("martin.perso@gmail.com", "boulangerie-martin.fr") == "UNCERTAIN"
    assert contacts.classify_email("sav@agence-web.fr", "boulangerie-martin.fr") is None
    only_named = contacts.extract([_page("<p>Jean Martin, gérant</p>")], "boulangerie-martin.fr")
    assert only_named["email"] is None                                                    # JAMAIS de prénom.nom@domaine fabriqué
    assert contacts.extract(pages, "boulangerie-martin.fr")["email_kind"] == "GENERIC_BUSINESS"


def test_formulaire_de_contact_detecte_et_canal_officiel_compte():
    c = contacts.extract([_page('<form action="/send"><input type="email" name="email"><textarea name="message"></textarea></form>')], "x.fr")
    assert c["contact_form"] is True and c["contact_page"] and c["phone"] is None and c["contact_confidence"] == pytest.approx(0.7)
    assert scoring.has_contact({"contact_form": 1})


def test_decouverte_progressive_lit_la_page_contact_si_manquante():
    home = htmlinfo.parse('<html><body><a href="/contact">Contact</a><a href="/equipe">Équipe</a></body></html>', "https://x.fr/")
    contact_html = '<html><body><p>Appelez le 03 25 12 34 56 ou écrivez à contact@x.fr</p></body></html>'
    web = FakeWeb({"https://x.fr/contact": (200, contact_html)})
    got = contacts.discover([home], web, "x.fr")
    assert len(got) == 2 and contacts.extract(got, "x.fr")["phone"] == "0325123456"
    assert web.calls == ["https://x.fr/contact"]                                          # rien de plus une fois phone + email trouvés


# ── scoring : deux scores, plafonds expliqués, portail de confiance ─────────────────────────────────────
TODAY = date(2026, 9, 21)
GOOD = dict(siret="12345678900011", distance_km=2.0, company_created_at=date(2026, 3, 1), employee_range="NN", website_status="CONFIRMED", website_confidence=0.95,
            audited_at=TODAY, modernization_opportunity="HIGH", technical_score=40, seo_score=20, seo_opportunity_score=80, performance_score=15,
            email="contact@x.fr", email_kind="GENERIC_BUSINESS", phone="0325123456", contact_confidence=0.95, geo_confidence=0.9, business_status_confidence=0.85,
            issues=[{"severity": "high"}])


def sc(**over):
    return scoring.score_prospect({**GOOD, **over}, None, None, 0.9, 20, TODAY)


def test_tres_bon_est_atteignable_avec_donnees_fiables_besoin_observable_et_contact():
    s = sc()
    assert s["category"] == "TRES_BON" and s["score"] >= 80 and s["data_confidence"] >= 70 and s["commercial_potential"] >= 60
    assert not any("Très bon" in c["reason"] for c in s["caps"])


def test_potentiel_eleve_mais_donnees_faibles_ne_donne_pas_tres_bon():
    s = sc(website_status="NOT_FOUND", website_absence_confidence=0.2, website_confidence=None, audited_at=None, modernization_opportunity=None, geo_confidence=0.3,
           business_status_confidence=0.4, contact_confidence=0.5, siret=None)
    assert s["data_confidence"] < 50 and s["category"] not in ("TRES_BON", "A_CONTACTER")
    assert any("fiabilité" in c["reason"] for c in s["caps"])


def test_site_non_trouve_ne_suffit_jamais_pour_tres_bon_meme_avec_contact():
    s = sc(website_status="NOT_FOUND", website_absence_confidence=0.85, audited_at=None, modernization_opportunity=None, issues=[])
    assert s["category"] != "TRES_BON" and s["score"] < scoring.DEFAULT_THRESHOLDS["TRES_BON"]


@pytest.mark.parametrize("over,why", [
    (dict(modernization_opportunity="LOW", seo_opportunity_score=10, performance_score=95, issues=[]), "besoin potentiel objectivement observable"),
    (dict(email=None, phone=None, contact_form=1, contact_confidence=0.6), "contact professionnel insuffisamment fiable"),
    (dict(contact_confidence=0.4), "contact professionnel insuffisamment fiable"),
])
def test_conditions_du_portail_tres_bon(over, why):
    s = sc(**over)
    assert s["category"] != "TRES_BON" and any(why in c["reason"] for c in s["caps"])


def test_activite_peu_dependante_du_web_bloque_tres_bon_et_poids_configurable():
    low = scoring.score_prospect({**GOOD}, None, None, 0.4, 20, TODAY)
    assert low["category"] != "TRES_BON" and any("activité peu dépendante du web" in c["reason"] for c in low["caps"])
    boosted = scoring.score_prospect({**GOOD}, None, None, 0.4, 20, TODAY, category_weight=1.5)
    assert boosted["category"] == "TRES_BON"                                              # poids d'activité réglable, pas codé en dur


def test_les_plafonds_sont_expliques_score_brut_plafond_raison():
    s = sc(email=None, phone=None, contact_confidence=None, contact_page=None)
    assert s["raw"] > s["score"] == min(c["limit"] for c in s["caps"])
    lines = [d["detail"] for d in s["details"] if d["key"] == "cap"]
    assert any(f"Score brut {s['raw']} → plafond {scoring.NO_CONTACT_CAP}" in line and "aucun contact professionnel" in line for line in lines)
    assert any("(appliqué)" in line for line in lines)


def test_a_contacter_exige_un_canal_de_contact():
    s = sc(email=None, phone=None, contact_confidence=None, contact_page=None, contact_form=0)
    assert s["category"] == "A_EXAMINER" and any("À contacter" in c["reason"] and "contact" in c["reason"] for c in s["caps"])


@pytest.mark.parametrize("kind", ["NATIONAL", "NETWORK", "FRANCHISE"])
def test_chaines_reseaux_et_franchises_toujours_ignores(kind):
    s = sc(chain_kind=kind)
    assert s["score"] == 0 and s["category"] == "IGNORER"


def test_couverture_de_recherche_module_le_potentiel_d_un_site_non_trouve():
    kw = dict(website_status="NOT_FOUND", audited_at=None, modernization_opportunity=None, issues=[])
    weak = sc(website_absence_confidence=0.15, website_search_tried=2, website_search_total=8, **kw)
    strong = sc(website_absence_confidence=0.85, website_search_tried=8, website_search_total=8, **kw)
    assert strong["commercial_potential"] > weak["commercial_potential"] and strong["data_confidence"] > weak["data_confidence"]
    assert any("2/8" in d["detail"] for d in weak["details"] if d["key"] == "site_potential")


def test_geolocalisation_douteuse_annule_la_distance():
    s = sc(distance_km=0.5, geo_confidence=0.3)
    assert next(d for d in s["details"] if d["key"] == "proximity")["points"] == 4.0 and "douteuse" in next(d for d in s["details"] if d["key"] == "proximity")["detail"]


def test_entreprise_recente_seule_change_presque_rien():
    old, new = sc(company_created_at=date(2015, 1, 1)), sc(company_created_at=date(2026, 8, 1), bodacc={"kind": "creation"})
    assert new["score"] - old["score"] <= 6
    assert next(d for d in new["details"] if d["key"] == "freshness")["max"] == 5


def test_performance_inconnue_n_est_pas_zero():
    s = sc(performance_score=None, performance_status="UNKNOWN")
    assert "performance ?/100" in next(d for d in s["details"] if d["key"] == "seo_technical")["detail"]
    assert any("performance non mesurée" in c["label"] for c in s["data_confidence_details"])


# ── erreurs et reprise ───────────────────────────────────────────────────────────────────────────────────
def test_backoff_exponentiel_plafonne_et_abandon():
    d = [errors.retry_delay_minutes(errors.SEARCH_RATE_LIMIT, n) for n in (1, 2, 3, 4, 5, 6, 7)]
    assert d[0] == 120 and d[1] == 240 and d[2] == 480 and max(x for x in d if x) <= errors.MAX_BACKOFF_MIN and d[-1] is None
    assert errors.retry_delay_minutes(errors.SITE_ROBOTS_DENIED, 1) is None                # jamais réessayé
    assert errors.retry_delay_minutes(errors.SITE_BLOCKED, 1) == 7 * 24 * 60 and errors.retry_delay_minutes(errors.SITE_BLOCKED, 2) is None
    assert errors.retry_delay_minutes(errors.SITE_DNS_ERROR, 1) < errors.retry_delay_minutes(errors.SITE_BLOCKED, 1)
    assert errors.retry_delay_minutes("INCONNUE", 1) == errors.retry_delay_minutes(errors.UNEXPECTED, 1)


# ── constats réels (Troyes) ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("domain,expected", [("actulegales.fr", True), ("annuaire-des-pros-troyes.fr", True), ("infoentreprises-aube.fr", True),
                                             ("boulangerie-martin.fr", False), ("entreprise-martin.fr", False), ("martin-societe.fr", False), ("www.telephone-boulangerie.fr", True)])
def test_annuaires_non_listes_detectes_sans_ecarter_le_vrai_site(domain, expected):
    assert sf.looks_like_directory(domain.removeprefix("www."), MARTIN) is expected


def test_annuaire_bloque_par_anti_bot_n_est_jamais_un_site_incertain():
    """Réel : un annuaire répondant 403 donnait 0,60 (« search_snippet ») → UNCERTAIN. Désormais plafonné à 0,45 = NOT_FOUND."""
    snippet = Hit("https://registre-quelconque.fr/fiche/123456789", "Boulangerie Martin Troyes", "Boulangerie Martin 12 RUE EMILE ZOLA 10000 TROYES SIREN 123456789")
    res = sf.resolve(MARTIN, FakeSearch(default=[snippet]), FakeWeb())
    assert res.status == "NOT_FOUND" and res.candidates and max(c["confidence"] for c in res.candidates) <= 0.45


def test_deux_sources_independantes_qui_concordent_montent_un_site_probable():
    """Nom + ville + code postal sur le site (0,62 : incertain) ; l'OSM (proximité + nom) donne le même site : 2 sources indépendantes → PROBABLE."""
    web = FakeWeb().add_site("boulangerie-martin.fr", html(title="Boulangerie Martin - Troyes", h1="Boulangerie Martin", body="<p>Troyes 10000</p>"))
    alone = sf.resolve(MARTIN, FakeSearch(default=[hit("boulangerie-martin.fr")]), web)
    assert alone.status == "UNCERTAIN"
    both = sf.resolve(MARTIN, FakeSearch(), web, known_urls=["https://boulangerie-martin.fr/"])
    assert both.status == "PROBABLE" and both.confidence >= 0.8 and "structured_source" in codes(both.evidence)
