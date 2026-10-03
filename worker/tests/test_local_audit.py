"""Audit léger, coordonnées : mesures objectives, formulations prudentes, rien d'inventé."""
from datetime import date

import pytest

from worker.local import audit as au
from worker.local import contacts, htmlinfo

from local_fixtures import GOOD_JSONLD, TODAY, FakeWeb, html


class Clock:
    def __init__(self, *ticks):
        self.ticks = list(ticks)

    def __call__(self):
        return self.ticks.pop(0) if len(self.ticks) > 1 else self.ticks[0]


def run(home_html, domain="www.exemple-resto.fr", ms=200, **site):
    web = FakeWeb().add_site(domain, home_html, **site)
    scheme = "https" if site.get("https", True) else "http"
    return au.audit_site(f"{scheme}://{domain}/", web, clock=Clock(0.0, ms / 1000)), web


def codes(a):
    return {i.code for i in a.issues}


def test_bon_site_rapide_et_complet():
    a, web = run(html(jsonld=GOOD_JSONLD))
    assert a.reachable and a.https and a.http_redirects_to_https and a.viewport and a.sitemap and a.robots_txt == "ok"
    assert a.seo_score >= 90 and a.technical_score >= 95 and a.performance_score == 100 and a.modernization == "LOW"
    assert not [i for i in a.issues if i.severity in ("medium", "high")]
    assert au.seo_summary(a) == "aucun problème majeur détecté"
    assert len(web.calls) <= 4                                              # audit poli : au plus 4 requêtes


def test_site_lent_ne_touche_pas_au_seo():
    a, _ = run(html(jsonld=GOOD_JSONLD), ms=4200)
    assert "slow_response" in codes(a) and a.performance_score == 30
    assert a.seo_score >= 90                                                # lent mais bien référencé : métriques séparées


def test_seo_technique_incomplet_sans_dire_aucun_referencement():
    a, _ = run(html(desc=None, h1=None, canonical=False, og=False, jsonld=None), sitemap=None)
    assert {"meta_description_missing", "h1_missing", "sitemap_missing", "structured_data_missing"} <= codes(a)
    assert 30 <= a.seo_score < 60 and a.seo_opportunity_score == 100 - a.seo_score
    assert au.seo_summary(a) in ("bases SEO manquantes", "SEO technique améliorable")
    assert a.technical_score >= 95 and a.performance_score == 100          # le SEO faible n'abaisse ni la technique ni la performance


def test_site_rapide_mais_seo_mediocre_et_inversement():
    rapide_mauvais_seo, _ = run(html(title="Accueil", desc=None, h1=None), ms=150)
    assert rapide_mauvais_seo.performance_score == 100 and rapide_mauvais_seo.seo_score < 60
    lent_bon_seo, _ = run(html(jsonld=GOOD_JSONLD), ms=3500)
    assert lent_bon_seo.performance_score < 60 and lent_bon_seo.seo_score >= 90


def test_modernisation_uniquement_sur_indices_objectifs():
    old = html(viewport=False, head_extra="<!--[if lt IE 9]><script src='html5shiv.js'></script><![endif]-->",
               body="<marquee>Promo</marquee><font color='red'>Bienvenue</font>", doctype="<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01//EN\">")
    a, _ = run(old, https=False)
    assert a.modernization == "HIGH" and a.https is False and "no_viewport" in codes(a) and "no_https" in codes(a)
    assert any("marquee" in e for e in a.modernization_evidence)


def test_copyright_ancien_seul_est_un_signal_faible_jamais_suffisant():
    a, _ = run(html(body="").replace("© 2026", "© 2009"))
    assert a.copyright_year == 2009 and a.modernization == "LOW"
    assert any("FAIBLE" in e for e in a.modernization_evidence)


def test_noindex_et_robots_bloquant():
    a, _ = run(html(noindex=True), robots="User-agent: *\nDisallow: /")
    assert {"noindex", "robots_blocks_all"} <= codes(a) and a.seo_score <= 30


def test_pas_de_redirection_http_vers_https():
    a, _ = run(html(), redirect_http=False)
    web = FakeWeb().add_site("www.exemple-resto.fr", html(), redirect_http=False)
    web.pages["http://www.exemple-resto.fr/"] = (200, html())               # la version http répond, sans redirection
    a = au.audit_site("https://www.exemple-resto.fr/", web, clock=Clock(0.0, 0.2))
    assert a.https and a.http_redirects_to_https is False and "no_https_redirect" in codes(a)


def test_site_inaccessible_et_erreur_http():
    a = au.audit_site("https://www.mort.fr/", FakeWeb(), clock=Clock(0.0, 0.1))
    assert not a.reachable and codes(a) == {"unreachable"} and a.technical_score == 0
    web = FakeWeb({"https://www.casse.fr/": (500, "<html>erreur</html>")})
    b = au.audit_site("https://www.casse.fr/", web, clock=Clock(0.0, 0.1))
    assert not b.reachable and "http_error" in codes(b)


def test_cms_et_ecommerce_identifies():
    a, _ = run(html(head_extra='<link rel="stylesheet" href="/wp-content/themes/x.css">', body='<a class="add_to_cart">Ajouter au panier</a>'))
    assert a.cms == "WordPress" and a.ecommerce


# ── coordonnées ──
def pages_with(body, url="https://www.boulangerie-martin.fr/", extra=None):
    ps = [htmlinfo.parse(html(body=body), url)]
    for u, b in (extra or {}).items():
        ps.append(htmlinfo.parse(html(body=b), u))
    return ps


def test_contact_generique_prioritaire_avec_source():
    c = contacts.extract(pages_with('<a href="/contact">Contact</a><p>gerant.perso@gmail.com contact@boulangerie-martin.fr 03 25 12 34 56</p>'), "boulangerie-martin.fr")
    assert (c["email"], c["email_kind"]) == ("contact@boulangerie-martin.fr", "GENERIC_BUSINESS")
    assert c["phone"] == "0325123456" and c["contact_page"].endswith("/contact") and c["contact_source_url"].startswith("https://www.boulangerie-martin.fr")


def test_adresse_grand_public_conservee_mais_signalee_et_domaines_tiers_ignores():
    c = contacts.extract(pages_with("<p>boulangerie.martin@gmail.com support@wix-agency.com noreply@boulangerie-martin.fr</p>"), "boulangerie-martin.fr")
    assert (c["email"], c["email_kind"]) == ("boulangerie.martin@gmail.com", "UNCERTAIN")


def test_aucun_contact():
    c = contacts.extract(pages_with("<p>Bienvenue</p>"), "boulangerie-martin.fr")
    assert not any(c.values())
    empty = contacts.extract([], "x.fr")
    assert empty["phone"] is None and empty["email"] is None and empty["contact_confidence"] is None and empty["evidence"] == []


