"""Site du domaine de l'adresse e-mail pro de la fiche (sitefinder.resolve_email_domain) : l'e-mail sur ce domaine est une preuve d'identité de
plus, sans jamais contourner les rejets ; messagerie gratuite, mauvais site, page d'hébergeur, domaine parqué : rien. Aucun accès réseau."""
from __future__ import annotations

from local_fixtures import FakeWeb, html

from worker.local import sitefinder as sf

CLUB = dict(siret="33333333300011", siren="333333333", company_name="SAS LE PALATIUM", trade_name="Palatium Club", city="TROYES", postal_code="10000",
            address="3 PLACE DE LA PAIX 10000 TROYES", phone=None, email="contact@leclub-evenements.com", bad_domains=[])
DOM = "leclub-evenements.com"


def _web(**kw):
    home = html(title="Accueil", h1="Bienvenue", desc="Soirées et événements", canonical=False, body="<p>Nous sommes à Troyes, 10000.</p>")
    return FakeWeb().add_site(DOM, home, **kw)


def test_domaine_de_l_email_avec_un_vrai_site_est_retenu():
    res = sf.resolve_email_domain(CLUB, _web())
    assert res.accepted and res.canonical_domain == DOM and res.url == f"https://{DOM}/"
    codes = {e["code"] for e in res.evidence}
    assert "email_domain_proof" in codes and "email_domain" in codes            # preuve lisible dans les evidence
    assert res.status == "PROBABLE"                                              # e-mail + ville + code postal : pas de nom dans le domaine
    assert res.candidates[0]["via"] == "email_domain"


def test_la_meme_page_sans_la_preuve_de_l_email_n_est_pas_retenue():
    """La preuve « e-mail sur le domaine » est ce qui fait passer le site : sans elle (e-mail d'une messagerie gratuite) la page ne suffit pas."""
    web = _web()
    assert sf.resolve_email_domain({**CLUB, "email": "palatium@gmail.com"}, web).status == "NOT_FOUND"
    assert web.calls == []                                                       # messagerie gratuite : le domaine n'est jamais lu


def test_site_sans_rapport_avec_l_entreprise_reste_a_trancher():
    web = FakeWeb().add_site(DOM, html(title="Accueil", h1="Bienvenue", desc="Bienvenue", canonical=False, body="<p>Bientôt des nouvelles.</p>"))
    res = sf.resolve_email_domain(CLUB, web)
    assert res.status == "UNCERTAIN" and res.url is None and res.candidates[0]["domain"] == DOM       # répond, mais ni nom ni ville : incertain


def test_page_d_hebergeur_en_construction_ne_donne_rien():
    ovh = html(title="Site en construction", h1="Site en construction", desc=None, canonical=False, body="<p>Ce site est en cours de construction. OVH</p>")
    assert sf.resolve_email_domain(CLUB, FakeWeb().add_site(DOM, ovh)).status == "NOT_FOUND"


def test_domaine_parque_ne_donne_rien():
    parked = html(title="palatium-club.com", h1="", desc=None, canonical=False, body="<p>Ce domaine est à vendre. Buy this domain.</p>")
    assert sf.resolve_email_domain(CLUB, FakeWeb().add_site(DOM, parked)).status == "NOT_FOUND"


def test_site_injoignable_ne_donne_rien():
    assert sf.resolve_email_domain(CLUB, FakeWeb()).status == "NOT_FOUND"


def test_domaine_deja_signale_mauvais_site_est_ignore():
    web = _web()
    assert sf.resolve_email_domain({**CLUB, "bad_domains": [DOM]}, web).status == "NOT_FOUND" and web.calls == []


def test_adresse_lue_sur_ce_site_meme_n_est_pas_une_preuve():
    web = _web()
    assert sf.resolve_email_domain({**CLUB, "email_source_url": f"https://www.{DOM}/contact"}, web).status == "NOT_FOUND" and web.calls == []


def test_le_domaine_de_l_email_ne_contourne_pas_le_rejet_d_une_autre_societe():
    legal = html(title="Mentions légales", h1="Mentions légales", body="<p>Site édité par la société ACME HOLDING SAS, SIREN 999 999 999, 8 rue Haute 69000 Lyon.</p>")
    web = FakeWeb().add_site(DOM, html(title="Palatium Club", h1="Palatium Club", body="<p>Troyes 10000</p>"), legal=legal)
    res = sf.resolve_email_domain(CLUB, web)
    assert not res.accepted and res.status == "UNCERTAIN" and res.url is None   # site d'une autre société : ni retenu, ni « aucun site »


def test_repli_sur_www_puis_http():
    web = FakeWeb().add_site(f"www.{DOM}", html(title="Palatium Club", h1="Palatium Club", body="<p>Troyes 10000</p>"))
    res = sf.resolve_email_domain(CLUB, web)
    assert res.accepted and res.canonical_domain == DOM and web.calls[0] == f"https://{DOM}/"
