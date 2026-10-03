"""Probabilité d'ACHAT : signaux (pourquoi maintenant), budget probable, apprentissage à partir des résultats, sites gratuits / d'annuaire."""
from datetime import date, datetime

import pytest

from worker.local import audit as au
from worker.local import budget, learning, scoring, signals

from local_fixtures import GOOD_JSONLD, FakeWeb, html

TODAY = date(2026, 9, 21)
BASE = dict(siret="12345678900011", distance_km=3.0, company_created_at=date(2020, 1, 1), employee_range="NN", website_status="NOT_FOUND",
            website_absence_confidence=0.85, phone="0325123456")


def score(**over):
    learn = over.pop("learning", None)
    return scoring.score_prospect({**BASE, **over}, activity_web=0.9, radius_km=20, today=TODAY, learning=learn)


def line(s, key):
    return next(d for d in s["details"] if d["key"] == key)


# ── Signaux d'achat ─────────────────────────────────────────────────────────────────────────────────────
def test_reprise_recente_est_le_signal_le_plus_fort_et_s_eteint_avec_le_temps():
    fresh = signals.detect({"bodacc": {"events": [{"kind": "takeover", "published": "2026-07-10"}]}}, TODAY)
    old = signals.detect({"bodacc": {"events": [{"kind": "takeover", "published": "2025-12-01"}]}}, TODAY)
    stale = signals.detect({"bodacc": {"events": [{"kind": "takeover", "published": "2024-01-01"}]}}, TODAY)
    assert fresh[0]["code"] == "takeover" and fresh[0]["strength"] == 1.0
    assert old[0]["strength"] == 0.5 and stale == []


SEARCHED = {"website_status": "NOT_FOUND", "website_absence_confidence": 0.85}


def test_ouverture_recente_sans_site():
    assert signals.detect({**SEARCHED, "company_created_at": date(2026, 6, 2)}, TODAY)[0]["code"] == "new_no_site"
    assert signals.detect({**SEARCHED, "company_created_at": date(2025, 6, 2)}, TODAY) == []                            # 15 mois : trop ancien
    shallow = {"website_status": "NOT_FOUND", "website_absence_confidence": 0.4, "company_created_at": date(2026, 6, 2), "socials": ["https://facebook.com/x"]}
    assert signals.detect(shallow, TODAY) == []                                  # recherche insuffisante : « aucun site » n'est pas encore un fait
    assert signals.detect({"website_status": "CONFIRMED", "company_created_at": date(2026, 6, 2)}, TODAY) == []         # il a déjà un site


def test_site_en_panne_reseaux_seuls_et_site_sans_contact():
    assert signals.detect({"website_status": "UNREACHABLE", "error_category": "SITE_DNS_ERROR"}, TODAY)[0]["detail"].startswith("le domaine ne répond plus")
    assert signals.detect({**SEARCHED, "socials": ["https://facebook.com/x"]}, TODAY)[0]["code"] == "socials_only"
    audited = {"website_status": "CONFIRMED", "audited_at": datetime(2026, 9, 1)}
    assert [s["code"] for s in signals.detect(audited, TODAY)] == ["no_contact_way"]
    assert signals.detect({**audited, "contact_form": 1}, TODAY) == []


def test_plusieurs_signaux_se_cumulent_sans_depasser_le_maximum():
    assert signals.bonus([]) == 0
    assert signals.bonus([{"strength": 0.7}, {"strength": 0.6}]) == pytest.approx(0.85)
    assert signals.bonus([{"strength": 1.0}, {"strength": 0.9}]) == 1.0


def test_signal_d_achat_augmente_le_score_et_s_explique():
    plain = score()
    takeover = score(bodacc={"kind": "change", "events": [{"kind": "takeover", "published": "2026-08-01"}]})
    assert line(plain, "buy_signals")["points"] == 0
    assert line(takeover, "buy_signals")["points"] == signals.BONUS_MAX and "Reprise" in line(takeover, "buy_signals")["detail"]
    assert takeover["raw"] == plain["raw"] + signals.BONUS_MAX and takeover["signals"][0]["code"] == "takeover"
    assert takeover["score"] < scoring.DEFAULT_THRESHOLDS["TRES_BON"]                                  # « Très bon » exige toujours des données fiables


def test_les_ajustements_departagent_les_prospects_plafonnes_sans_franchir_les_verrous():
    rich = dict(contact_confidence=0.9, business_status_confidence=0.9, geo_confidence=0.9)
    plain = score(**rich)
    boosted = score(**rich, activity_key="plombiers", bodacc={"events": [{"kind": "move", "published": "2026-08-01"}]})
    poor = score(**rich, activity_key="bars")
    assert plain["score"] == scoring.NO_SITE_CAP and poor["score"] < plain["score"] < boosted["score"] < 80
    no_contact = score(phone=None, activity_key="plombiers", bodacc={"events": [{"kind": "takeover", "published": "2026-08-01"}]})
    assert no_contact["score"] <= scoring.DEFAULT_THRESHOLDS["A_CONTACTER"] - 1                       # sans contact : toujours au mieux « À examiner »


def test_chaine_reste_a_zero_malgre_les_signaux():
    s = score(is_chain=1, chain_kind="NATIONAL", bodacc={"events": [{"kind": "takeover", "published": "2026-08-01"}]})
    assert s["score"] == 0 and s["category"] == "IGNORER"


# ── Budget ──────────────────────────────────────────────────────────────────────────────────────────────
def test_budget_selon_le_metier_le_chiffre_d_affaires_et_l_effectif():
    assert budget.estimate({"activity_key": "plombiers"})["level"] == "élevé"
    assert budget.estimate({"activity_key": "bars"})["level"] == "serré"
    assert budget.estimate({})["level"] == "inconnu"
    rich = budget.estimate({"activity_key": "restaurants", "revenue": 450_000, "revenue_year": 2024, "employee_range": "02"})
    poor = budget.estimate({"activity_key": "restaurants", "revenue": 18_000, "revenue_year": 2024})
    assert rich["points"] == 6 and "450 k€" in rich["detail"] and poor["points"] == -5 and "budget serré" in poor["detail"]
    assert budget.estimate({"activity_key": "plombiers", "revenue": 900_000, "employee_range": "03"})["points"] <= budget.MAX_POINTS


def test_budget_dans_le_score():
    assert line(score(activity_key="plombiers"), "budget")["points"] == 4
    assert score(activity_key="plombiers")["raw"] > score(activity_key="bars")["raw"]


# ── Apprentissage ───────────────────────────────────────────────────────────────────────────────────────
NOW = datetime(2026, 9, 21)


def row(metier, status, city="Troyes", response=None, reason=None, contacted=datetime(2026, 9, 15), sigs=()):
    return {"activity_key": metier, "city": city, "website_status": "NOT_FOUND", "status": status, "response_status": response,
            "feedback_reason": reason, "contacted_at": contacted, "last_contacted_at": contacted, "buy_signals": [{"code": c} for c in sigs]}


def test_ce_qui_compte_comme_resultat():
    assert learning.outcome(row("x", "WON")) == 1.0 and learning.outcome(row("x", "REPLIED")) == 0.5
    assert learning.outcome(row("x", "LOST")) == 0.0 and learning.outcome(row("x", "CONTACTED", response="NO_ANSWER")) == 0.0
    assert learning.outcome(row("x", "CONTACTED"), NOW) is None                                          # contacté il y a 6 jours : on attend
    assert learning.outcome(row("x", "CONTACTED", contacted=datetime(2026, 8, 1)), NOW) == 0.0          # > 21 jours sans réponse
    assert learning.outcome(row("x", "LOST", reason="wrong_site")) is None                              # erreur de données, pas un verdict
    assert learning.outcome(row("x", "TO_CONTACT")) is None


def test_apprentissage_inactif_tant_qu_il_y_a_trop_peu_de_resultats():
    model = learning.compute([row("plombiers", "WON"), row("plombiers", "WON"), row("bars", "LOST")], NOW)
    pts, why = learning.adjustment(model, {"activity_key": "plombiers"}, [])
    assert not model["active"] and pts == 0 and "pas encore assez de résultats" in why


def test_apprentissage_favorise_ce_qui_repond_et_penalise_le_reste():
    rows = ([row("plombiers", "WON"), row("plombiers", "REPLIED"), row("plombiers", "INTERESTED"), row("plombiers", "LOST"), row("plombiers", "REPLIED")]
            + [row("bars", "LOST") for _ in range(6)] + [row("fleuristes", "REPLIED"), row("fleuristes", "LOST")])
    model = learning.compute(rows, NOW)
    assert model["active"] and model["features"]["metier:fleuristes"]["points"] == 0                  # 2 exemples : trop peu pour conclure
    up, why = learning.adjustment(model, {"activity_key": "plombiers", "city": "Dijon"}, [])
    down, _ = learning.adjustment(model, {"activity_key": "bars", "city": "Dijon"}, [])
    assert 0 < up <= learning.PER_FEATURE_MAX and -learning.PER_FEATURE_MAX <= down < 0
    assert "métier « plombiers » 4/5" in why
    s_up, s_down = score(activity_key="plombiers", learning=model), score(activity_key="plombiers", learning=None)
    assert line(s_up, "learning")["points"] == up and s_up["raw"] > s_down["raw"]


def test_ajustement_total_borne():
    feats = {f"signal:{c}": {"points": 5.0, "label": c, "wins": 5, "n": 5} for c in ("takeover", "move", "rename")}
    model = {"active": True, "outcomes": 40, "prior": 0.2, "features": {**feats, "metier:plombiers": {"points": 5.0, "label": "p", "wins": 5, "n": 5}}}
    pts, _ = learning.adjustment(model, {"activity_key": "plombiers"}, ["takeover", "move", "rename"])
    assert pts == learning.TOTAL_MAX


def test_messages_mesures_a_part():
    rows = [{**row("plombiers", "REPLIED"), "draft_kind": "sans_site"}, {**row("plombiers", "LOST"), "draft_kind": "sans_site"},
            {**row("bars", "LOST"), "draft_kind": "site_ameliorable"}]
    assert learning.compute(rows, NOW)["messages"] == {"sans_site": {"n": 2, "wins": 1}, "site_ameliorable": {"n": 1, "wins": 0}}


# ── Audit : sites gratuits / d'annuaire ─────────────────────────────────────────────────────────────────
def test_site_sur_sous_domaine_gratuit_ou_fourni_par_un_annuaire():
    web = FakeWeb().add_site("salon-lea.jimdosite.com", html(jsonld=GOOD_JSONLD))
    a = au.audit_site("https://salon-lea.jimdosite.com/", web)
    assert a.free_subdomain == "jimdosite.com" and "free_subdomain" in {i.code for i in a.issues}
    web = FakeWeb().add_site("www.garage-martin.fr", html(jsonld=GOOD_JSONLD).replace("</body>", "<footer>Site réalisé par Solocal</footer></body>"))
    a = au.audit_site("https://www.garage-martin.fr/", web)
    assert a.directory_site and "directory_site" in {i.code for i in a.issues} and not a.free_subdomain
    good = au.audit_site("https://www.exemple-resto.fr/", FakeWeb().add_site("www.exemple-resto.fr", html(jsonld=GOOD_JSONLD)))
    assert not good.free_subdomain and not good.directory_site
    assert signals.detect({"website_status": "CONFIRMED", "contact_form": 1, "issues": [i.__dict__ for i in a.issues]}, TODAY)[0]["code"] == "site_diy"


# ── Qualité : activités bruitées, favorisées, exclusions ─────────────────────────────────────────────────
def test_activite_bruitee_sans_vrai_signal_est_ecartee():
    good_site = dict(website_status="CONFIRMED", website_confidence=0.95, audited_at=datetime(2026, 9, 1), modernization_opportunity="LOW",
                     technical_score=90, seo_score=85, email="contact@resto.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9)
    resto = score(activity_key="restaurants", **good_site)
    assert resto["score"] <= scoring.NOISY_CAP and resto["category"] == "IGNORER" and any("vrai signal" in c["reason"] for c in resto["caps"])
    assert score(activity_key="plombiers", **good_site)["score"] > scoring.NOISY_CAP                  # un artisan au site correct reste visible
    assert score(activity_key="restaurants", **{**good_site, "modernization_opportunity": "HIGH"})["score"] > scoring.NOISY_CAP
    assert score(activity_key="restaurants")["score"] > scoring.NOISY_CAP                               # aucun site après une vraie recherche
    assert score(activity_key="restaurants", website_absence_confidence=0.3)["score"] <= scoring.NOISY_CAP  # recherche trop maigre : pas un signal


def test_activites_favorisees_et_bruitees():
    from worker.local import naf
    assert naf.default_weight("plombiers") > 1 > naf.default_weight("bars") and naf.default_weight(None) == 1
    assert not (naf.FAVORED & naf.NOISY) and (naf.FAVORED | naf.NOISY) <= set(naf.CATALOG)


@pytest.mark.parametrize("naf_code,legal,excluded", [("68.20B", "6540", True), ("68.31Z", "6540", True), ("68.31Z", "5710", False),
                                                     ("64.20Z", "5710", True), ("96.02A", "7210", True), ("96.02A", "1000", False)])
def test_structures_sans_interet_commercial_exclues(naf_code, legal, excluded):
    from worker.local import naf
    assert bool(naf.excluded_reason(naf_code, "X", legal_category=legal)) is excluded


# ── Réseaux sociaux vérifiés ─────────────────────────────────────────────────────────────────────────────
from worker.local import htmlinfo, socials  # noqa: E402
from worker.local.sitefinder import Hit  # noqa: E402

COMPANY = {"trade_name": "Plomberie Durand", "company_name": "SARL DURAND PLOMBERIE", "city": "Troyes", "postal_code": "10000", "manager_name": "Paul Durand"}


@pytest.mark.parametrize("url,expected", [
    ("https://fr-fr.facebook.com/plomberiedurand/?ref=page", "https://www.facebook.com/plomberiedurand"),      # version de langue
    ("https://www.facebook.com/plomberiedurand/?ref=page_internal", "https://www.facebook.com/plomberiedurand"),
    ("https://m.facebook.com/profile.php?id=10006&ref=x", "https://www.facebook.com/profile.php?id=10006"),
    ("https://www.facebook.com/sharer/sharer.php?u=x", None), ("https://www.facebook.com/groups/troyes", None),
    ("https://www.instagram.com/p/Cx12/", None), ("https://instagram.com/plomberie.durand", "https://www.instagram.com/plomberie.durand"),
    ("https://fr.linkedin.com/company/plomberie-durand/about", "https://www.linkedin.com/company/plomberie-durand"),
    ("https://www.linkedin.com/posts/x-123", None), ("https://www.facebook.com/", None)])
def test_seuls_les_profils_sont_retenus(url, expected):
    assert socials.normalize(url) == expected


def test_profil_trouve_par_recherche_seulement_s_il_nomme_l_entreprise_et_sa_ville():
    hits = [Hit("https://www.facebook.com/plomberiedurand", "Plomberie Durand | Troyes", "Plombier chauffagiste"),
            Hit("https://www.facebook.com/plomberiedurand.lyon", "Plomberie Durand | Lyon", "Plombier à Lyon"),          # homonyme ailleurs
            Hit("https://www.instagram.com/lesplombiers", "Les plombiers de Troyes", "Troyes"),                     # nom absent
            Hit("https://www.linkedin.com/in/paul-durand-123", "Paul Durand - Gérant - Troyes", "Troyes"),
            Hit("https://www.linkedin.com/in/jean-martin", "Jean Martin - Plomberie Durand Troyes", "")]              # pas le dirigeant
    got = socials.from_hits(COMPANY, hits)
    assert [(x["network"], x["url"]) for x in got] == [("Facebook", "https://www.facebook.com/plomberiedurand"),
                                                       ("LinkedIn", "https://www.linkedin.com/in/paul-durand-123")]
    assert all(x["via"] == "search" and x["confidence"] == socials.SEARCH_CONF for x in got)


def test_profils_lies_par_le_site_officiel_priment():
    page = htmlinfo.parse('<html><body><a href="https://www.instagram.com/plomberie.durand/">Insta</a>'
                          '<a href="https://www.facebook.com/sharer.php?u=x">Partager</a><a href="https://facebook.com/PlomberieDurand10">FB</a></body></html>',
                          "https://www.plomberie-durand.fr/")
    site = socials.from_pages([page])
    assert [(x["network"], x["via"]) for x in site] == [("Facebook", "site"), ("Instagram", "site")]
    merged = socials.merge([{"network": "Facebook", "url": "https://www.facebook.com/autre", "via": "search", "confidence": 0.7}], site)
    assert merged[0]["url"] == "https://www.facebook.com/PlomberieDurand10"


# ── Priorité de ciblage : refonte d'abord ───────────────────────────────────────────────────────────────
def test_priorite_refonte_d_abord_ou_sans_site_d_abord():
    dated = dict(website_status="CONFIRMED", website_confidence=0.95, audited_at=datetime(2026, 9, 1), modernization_opportunity="HIGH",
                 email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9, activity_key="plombiers")
    nosite = dict(activity_key="plombiers", contact_confidence=0.9)
    sc = lambda over, focus: scoring.score_prospect({**BASE, **over}, activity_web=0.9, radius_km=20, today=TODAY, focus=focus)  # noqa: E731
    assert sc(dated, "redesign")["score"] > sc(nosite, "redesign")["score"]
    assert sc(nosite, "no_site")["score"] >= sc(nosite, "redesign")["score"] and sc(dated, "redesign")["score"] > sc(dated, "no_site")["score"]
    assert "priorité : refonte" in line(sc(dated, "redesign"), "site_potential")["detail"]


def test_site_fait_avec_wix_sur_son_domaine_est_un_argument_de_refonte():
    web = FakeWeb().add_site("www.salon-lea.fr", html(jsonld=GOOD_JSONLD, head_extra='<link href="https://static.wixstatic.com/x.css" rel="stylesheet">'))
    a = au.audit_site("https://www.salon-lea.fr/", web)
    assert a.cms == "Wix" and "site_builder" in {i.code for i in a.issues} and any("créateur de sites" in e for e in a.modernization_evidence)
    s = signals.detect({"website_status": "CONFIRMED", "contact_form": 1, "issues": [i.__dict__ for i in a.issues]}, TODAY)
    assert s[0]["code"] == "site_builder" and s[0]["strength"] < signals.REAL_SIGNAL_MIN and s[0]["detail"] == "Wix"   # signal faible, bien nommé
    assert signals.real_signal({"website_status": "CONFIRMED"}, s, TODAY) is None           # constaté en réel : un resto au site Wix correct n'est pas une cible


# ── Exclusions sur ce que l'entreprise dit d'elle-même, pas sur ses mentions légales ──────────────────────
def test_mentions_legales_avec_hebergeur_n_excluent_pas_un_plombier():
    from worker.local import runner
    plumber = htmlinfo.parse('<html><head><title>Aube Fluide - Plombier chauffagiste à Troyes</title><meta name="description" content="Dépannage plomberie"></head>'
                             '<body><h1>Plomberie</h1><footer>Hébergeur : OVH. Conception et développement web : Agence X</footer></body></html>', "https://aubefluide.fr/")
    agency = htmlinfo.parse('<html><head><title>Agence web à Troyes - création de sites</title></head><body><h1>Agence web</h1></body></html>', "https://agence.fr/")
    from worker.local import naf
    assert naf.excluded_reason(None, None, runner._self_description(plumber)) is None                  # constaté en réel : exclu à tort avant
    assert naf.excluded_reason(None, None, runner._self_description(agency))


# ── Franchisé sous un autre nom : enseigne de chaîne du même métier à la même adresse (OpenStreetMap) ─────
def test_franchise_reconnue_par_l_enseigne_a_la_meme_adresse():
    from worker.local import osm
    places = [osm.OsmPlace("n1", "Subway", 48.29700, 4.07400, kind="fast_food"), osm.OsmPlace("n2", "McDonald's", 48.29800, 4.07400)]
    mbe = {"latitude": 48.29701, "longitude": 4.07401, "naf_code": "56.10C"}
    assert osm.brand_at_address(mbe, places)["brand"] == "subway"                                         # « M.B.E.SUB » = un Subway (réel)
    assert osm.brand_at_address({**mbe, "naf_code": "96.02A"}, places) is None                             # voisin d'un autre métier : jamais
    assert osm.brand_at_address({"latitude": 48.29750, "longitude": 4.07400, "naf_code": "56.10C"}, places) is None   # 55 m : un voisin
