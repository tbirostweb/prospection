"""Défauts concrets relevés par l'audit et leur effet sur le score : certains, vérifiables, jamais supposés."""
from datetime import date

from worker.local import audit as au, scoring

from local_fixtures import FakeWeb, html

TEXTE = "<p>" + "Nous réalisons vos travaux avec soin depuis de longues années. " * 12 + "</p>"
TODAY = date(2026, 10, 30)


def run(home, **kw):
    web = FakeWeb().add_site("www.exemple.fr", home)
    return au.audit_site("https://www.exemple.fr/", web, **kw)


def codes(a):
    return {i.code for i in a.issues}


def test_coordonnees_absentes_de_l_accueil():
    assert "no_contact_visible" in codes(run(html(body=TEXTE)))
    assert not run(html(body=TEXTE)).contact_visible
    assert "no_contact_visible" not in codes(run(html(body=TEXTE + "<p>Appelez-nous au 03 25 12 34 56</p>")))
    assert "no_contact_visible" not in codes(run(html(body=TEXTE + '<a href="/contact">Contact</a>')))
    assert "no_contact_visible" not in codes(run(html(body=TEXTE + "<p>ecrire : jean@exemple.fr</p>")))
    assert "no_contact_visible" not in codes(run(html(body="<p>Bienvenue</p>")))          # page quasi vide : doute, rien relevé


def test_generateur_ancien_est_une_technologie_obsolete():
    a = run(html(head_extra='<meta name="generator" content="Microsoft FrontPage 4.0">', body=TEXTE + "tel 03 25 12 34 56"))
    assert any("FrontPage" in s for s in a.old_signals)
    assert any(i.code == "old_tech" for i in a.issues)
    assert not run(html(head_extra='<meta name="generator" content="WordPress 6.5">', body=TEXTE)).old_signals


def test_annee_de_copyright_future_ignoree():
    web = FakeWeb().add_site("www.exemple.fr", html(body="<footer>© 2099</footer>"))
    assert au.audit_site("https://www.exemple.fr/", web).copyright_year == 2019         # la fixture ajoute © 2019


def test_defauts_concrets():
    d = lambda **p: scoring.concrete_defects(p, TODAY)   # noqa: E731
    assert d(issues=[{"code": "no_viewport"}]) == ["no_viewport"]
    assert d(issues=[{"code": "sitemap_missing"}, {"code": "structured_data_missing"}]) == []          # réels mais obscurs
    assert d(issues=[{"code": "old_tech", "evidence": "balises <font> / <center> (mise en forme HTML ancienne)"}]) == []
    assert d(issues=[{"code": "old_tech", "evidence": "Flash / SWF"}]) == ["old_tech"]
    assert d(issues=[], audit={"copyright_year": 2023}) == ["copyright_old"]
    assert d(issues=[], audit={"copyright_year": 2024}) == []
    assert d(issues=[], lighthouse_performance=30) == ["lighthouse_slow"]


def test_site_sans_defaut_passe_en_priorite_basse_et_site_avec_defaut_monte():
    base = dict(website_status="CONFIRMED", website_confidence=0.95, audited_at=TODAY, modernization_opportunity="LOW", technical_score=97,
                seo_score=70, seo_opportunity_score=30, performance_score=90, email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9,
                activity_key="plombiers", company_created_at=date(2012, 1, 1), distance_km=5, audit={"copyright_year": 2026})
    sc = lambda **o: scoring.score_prospect({**base, **o}, activity_web=0.9, radius_km=20, today=TODAY, focus="redesign")   # noqa: E731
    clean = sc(issues=[])
    assert clean["score"] <= 50 and clean["category"] not in ("TRES_BON", "A_CONTACTER") and any("aucun défaut concret" in c["reason"] for c in clean["caps"])
    old = sc(issues=[], audit={"copyright_year": 2018})
    assert old["score"] >= clean["score"] + 15 and old["category"] in ("A_CONTACTER", "TRES_BON") and not any("aucun défaut concret" in c["reason"] for c in old["caps"])
    assert sc(issues=[{"code": "no_contact_visible"}])["score"] > clean["score"]
    assert sc()["score"] >= clean["score"]                  # issues inconnues (fiche ancienne) : jamais baissée à tort
