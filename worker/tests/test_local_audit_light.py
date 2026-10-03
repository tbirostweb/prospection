"""Audit léger : performance SANS Lighthouse, preuves SEO, gabarits de sites variés (WordPress, Wix, Shopify, PrestaShop, statique…), fail-safe."""
import pytest

from worker.local import audit as au

from local_fixtures import FakeWeb, html
from test_local_audit import Clock


def run(home_html, ms=200, headers=None, **site):
    web = FakeWeb().add_site("www.exemple-resto.fr", home_html, **site)
    if headers is not None:
        web.headers = headers
    return au.audit_site("https://www.exemple-resto.fr/", web, clock=Clock(0.0, ms / 1000))


def test_performance_legere_sans_lighthouse_avec_compression_et_cache():
    a = run(html(), headers={"content-encoding": "br", "cache-control": "max-age=600"})
    assert a.performance_status == "LIGHT" and a.performance_score == 100 and a.compression == "br" and a.cache_control == "max-age=600"
    assert "no_compression" not in {i.code for i in a.issues}
    big = run(html(body="<p>" + "x" * 40_000 + "</p>"), headers={})
    assert big.compression is None and "no_compression" in {i.code for i in big.issues}


def test_performance_inconnue_n_est_jamais_zero():
    web = FakeWeb().add_site("www.exemple-resto.fr", html())
    a = au.audit_site("https://www.exemple-resto.fr/", web, home=__import__("worker.local.htmlinfo", fromlist=["parse"]).parse(html(), "https://www.exemple-resto.fr/"))
    assert a.response_ms is None and a.performance_status == "UNKNOWN" and a.performance_score is None       # non mesuré ≠ mauvais


def test_beaucoup_de_ressources_est_un_signal_faible_pas_un_verdict():
    body = "".join(f'<script src="/a{i}.js"></script><img src="/i{i}.jpg">' for i in range(50))
    a = run(html(body=body))
    issue = next(i for i in a.issues if i.code == "many_resources")
    assert issue.severity == "low" and a.resources["scripts"] == 50 and a.resources["images"] == 50


def test_preuves_seo_detaillees_et_signal_prudent():
    good = run(html(jsonld='{"@type":"Bakery","name":"X"}'))
    assert {e["check"] for e in good.seo_evidence} >= {"title", "meta_description", "h1", "canonical", "indexable", "sitemap", "structured_data", "viewport"}
    assert good.seo_signal is None and all(e["detail"] for e in good.seo_evidence)
    bad = run(html(title="Accueil", desc=None, h1=None, canonical=False, og=False), sitemap=None)
    assert bad.seo_signal == "SEO_TECHNICAL_OPPORTUNITY"
    failed = {e["check"] for e in bad.seo_evidence if not e["ok"]}
    assert {"title", "meta_description", "h1"} <= failed


def test_presence_locale_sans_classement_google():
    a = run(html(jsonld='{"@type":"Restaurant","name":"X"}'))
    assert a.local_presence["local_business_schema"] is True and "aucun classement Google" in a.local_presence["note"]


@pytest.mark.parametrize("name,head,body,cms,ecom,modern", [
    ("WordPress", '<meta name="generator" content="WordPress 6.4">', '<link rel="stylesheet" href="/wp-content/themes/x/style.css">', "WordPress", False, "LOW"),
    ("WordPress ancien", '<meta name="generator" content="WordPress 3.9">', '<link rel="stylesheet" href="/wp-content/themes/x/style.css">', "WordPress", False, "MEDIUM"),
    ("Wix", "", '<script src="https://static.wixstatic.com/services/wix.js"></script>', "Wix", False, "LOW"),
    ("Shopify", "", '<script src="https://cdn.shopify.com/s/x.js"></script><form action="/cart/add">', "Shopify", True, "LOW"),
    ("PrestaShop", '<meta name="generator" content="PrestaShop">', '<script>var prestashop = {};</script>', "PrestaShop", True, "LOW"),
    ("statique ancien", "", "<marquee>Bienvenue</marquee><center><font>Ma page</font></center>", None, False, "HIGH"),
])
def test_gabarits_de_sites_courants(name, head, body, cms, ecom, modern):
    a = run(html(head_extra=head, body=body))
    assert a.reachable and (a.cms or None) == cms, (name, a.cms)
    assert a.ecommerce is ecom, name
    assert a.modernization == modern, (name, a.modernization_evidence)


def test_site_sans_https_ni_viewport_est_a_moderniser_avec_preuves():
    web = FakeWeb().add_site("vieux.fr", html(viewport=False, body="<font>x</font>"), https=False, sitemap=None)
    a = au.audit_site("http://vieux.fr/", web, clock=Clock(0.0, 0.2))
    assert a.https is False and a.modernization == "HIGH" and any("HTTP" in e for e in a.modernization_evidence)


def test_site_casse_ou_en_erreur_est_un_probleme_disponibilite_pas_un_crash():
    web = FakeWeb({"https://casse.fr/": (500, "")})
    a = au.audit_site("https://casse.fr/", web, clock=Clock(0.0, 0.2))
    assert not a.reachable and any(i.code == "http_error" for i in a.issues)
    dead = au.audit_site("https://mort.fr/", FakeWeb(), clock=Clock(0.0, 0.2))
    assert not dead.reachable and any(i.code == "unreachable" for i in dead.issues)


def test_page_rendue_en_javascript_ou_anti_bot_n_est_pas_un_mauvais_seo():
    """Réel (site marchand FR) : 14 Ko de HTML, aucun texte visible → h1 / meta description « absents » n'auraient rien prouvé."""
    shell = '<html><head><title>Cdiscount</title></head><body><div id="root"></div>' + "".join(f'<script src="/a{i}.js"></script>' for i in range(4)) + "</body></html>"
    a = run(shell)
    assert a.js_shell and a.seo_score is None and a.seo_opportunity_score is None and a.seo_signal is None
    assert "h1_missing" not in {i.code for i in a.issues} and "meta_description_missing" not in {i.code for i in a.issues}
    assert any(i.code == "js_shell" and i.severity == "info" for i in a.issues)
    assert any(e["ok"] is None for e in a.seo_evidence)
    wall = run('<html><head><title>Just a moment...</title></head><body><p>Verify you are human</p><script>cf-chl</script></body></html>')
    assert wall.js_shell and wall.seo_score is None
    normal = run(html(body="<p>" + "Bonjour, nous sommes une boulangerie de Troyes. " * 10 + "</p>"))
    assert not normal.js_shell and normal.seo_score is not None
