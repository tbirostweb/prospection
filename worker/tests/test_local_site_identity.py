"""Règle d'identité du site (sitefinder.identity_verdict / owner_verdict / check_site) : « l'entreprise doit figurer sur le site ».

Cas constatés en production (acceptés à tort avant la règle) : un garage associé au site d'un paysagiste, une usine troyenne au site de son
groupe espagnol, deux filiales au site de leur groupe, une page « Site en Maintenance » (certificat HTTPS de l'hébergeur). Et ce qui doit
rester accepté : un vrai site de TPE (nom + ville), un site avec seulement le SIRET, le site d'un groupe qui détaille l'établissement de la
ville, un chauffagiste qui fait de la « maintenance de chaudières ». Pages types, aucun appel réseau."""
from worker.local import errors
from worker.local import sitefinder as sf

from local_fixtures import MARTIN, FakeSearch, FakeWeb, html


def _google(company, url, web):
    """Site DÉCLARÉ sur la fiche Google (le chemin qui laissait passer ces cas) : vérifié sur le site, aucune recherche."""
    res = sf.resolve(company, FakeSearch(), web, strategy_budget=0, candidate_urls=[url])
    return res, sf.suggested_verdict(res, url)


class SslWeb(FakeWeb):
    """Faux réseau qui journalise ses échecs comme `fetch.Fetcher` : les hôtes de `bad_tls` ont un certificat HTTPS invalide."""

    def __init__(self, *a, bad_tls=(), **k):
        super().__init__(*a, **k)
        self.failures: list[tuple[str, str]] = []
        self.bad_tls = set(bad_tls)

    def __call__(self, url):
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.scheme == "https" and p.hostname in self.bad_tls:
            self.calls.append(url)
            self.failures.append((errors.SITE_SSL_ERROR, url))
            return None
        return super().__call__(url)


# ── 1. Sarl Auto 10 → site d'un paysagiste ──────────────────────────────────────────────────────────────────────
AUTO10 = dict(siret="41234567800012", siren="412345678", company_name="SARL AUTO 10", city="TROYES", postal_code="10000",
              address="5 AVENUE PIERRE BROSSOLETTE 10000 TROYES", phone="0325112233", activity_label="Commerce de voitures et de véhicules automobiles légers")
FUHRMANN = html(title="Fuhrmann Espaces Verts - Paysagiste dans l'Aube (10)", h1="Création et entretien de jardins", canonical=False,
                body="<p>Paysagiste dans l'Aube (10) depuis 10 ans : tonte, taille, élagage, création de massifs. Interventions autour de Troyes.</p>"
                     "<p>12 rue des Jardins 10410 Saint-Parres-aux-Tertres - 03 25 99 88 77</p>"
                     "<footer>© 2024 Fuhrmann Espaces Verts - Tous droits réservés</footer>")


def test_garage_auto_10_site_d_un_paysagiste_rejete_autre_entreprise():
    web = FakeWeb().add_site("www.fuhrmann-espacesverts.fr", FUHRMANN)
    assert sf.strict_name_tokens("SARL AUTO 10", AUTO10) == []                   # ni « sarl », ni « auto », ni « 10 » n'identifient l'entreprise
    res, verdict = _google(AUTO10, "https://www.fuhrmann-espacesverts.fr/", web)
    assert not res.accepted and res.url is None and verdict == "rejected:other_company"
    assert sf.check_site(AUTO10, "https://www.fuhrmann-espacesverts.fr/", web)[0] == "other_company"


def test_garage_auto_10_seulement_son_siret_sur_le_site_accepte():
    legal = html(title="Mentions légales", h1="Mentions légales", canonical=False,
                 body="<p>SARL AUTO 10 au capital de 8 000 € - SIRET 412 345 678 00012 - RCS Troyes</p>")
    home = html(title="Accueil", h1="Véhicules d'occasion", canonical=False, body='<a href="/mentions-legales">Mentions légales</a><p>Nos véhicules.</p>')
    web = FakeWeb().add_site("www.vo-occasions.fr", home, legal=legal)
    res, verdict = _google(AUTO10, "https://www.vo-occasions.fr/", web)
    assert res.accepted and verdict in ("confirmed", "probable")
    assert sf.check_site(AUTO10, "https://www.vo-occasions.fr/", web)[0] == "ok"


# ── 2. Garnica Troyes → site du groupe espagnol ─────────────────────────────────────────────────────────────────
GARNICA = dict(siret="44455566600019", siren="444555666", company_name="GARNICA FRANCE", trade_name="GARNICA TROYES", city="TROYES",
               postal_code="10000", address="ZI DES ECREVOLLES 10000 TROYES", phone="0325700000", activity_label="Fabrication de placage et de panneaux de bois")
GROUP_BODY = ("<p>Garnica is a leading manufacturer of poplar plywood panels for the furniture and the transport industries. We are a family company "
              "and our values are innovation and sustainability. Our plants are located in Spain and in France: Valencia de Don Juan, Baños de Río "
              "Tobía, Fuenmayor and Troyes. We export our products to more than 40 countries and we work with the best partners.</p>"
              "<footer>GARNICA PLYWOOD S.A. - CIF: A26000000 - Logroño (Spain)</footer>")


def test_usine_de_troyes_site_du_groupe_espagnol_rejete():
    web = FakeWeb().add_site("www.garnica.one", html(title="Garnica | Plywood manufacturer", h1="Garnica", lang="en", canonical=False, body=GROUP_BODY))
    res, verdict = _google(GARNICA, "https://www.garnica.one/", web)
    assert not res.accepted and verdict == "rejected:group_site"
    assert res.status == "UNCERTAIN"                                              # le nom y est : à trancher, jamais un message automatique


def test_site_de_groupe_avec_l_etablissement_de_la_ville_accepte():
    body = GROUP_BODY.replace("<footer>", "<p>Garnica Troyes : ZI des Écrevolles, 10000 Troyes - Tél. 03 25 70 00 00</p><footer>")
    web = FakeWeb().add_site("www.garnica.one", html(title="Garnica | Plywood manufacturer", h1="Garnica", lang="en", canonical=False, body=body))
    res, verdict = _google(GARNICA, "https://www.garnica.one/", web)
    assert res.accepted and verdict in ("confirmed", "probable")


# ── 3. Filiales → site de leur groupe ───────────────────────────────────────────────────────────────────────────
OMTEK = dict(siret="52345678900015", siren="523456789", company_name="OMTEK-EGDI", city="LA CHAPELLE-SAINT-LUC", postal_code="10600",
             address="3 RUE DE L'INDUSTRIE 10600 LA CHAPELLE-SAINT-LUC", activity_label="Ingénierie, études techniques")
SECABAT = dict(siret="53345678900013", siren="533456789", company_name="SECABAT", city="TROYES", postal_code="10000",
               address="8 RUE DES BAS TREVOIS 10000 TROYES", activity_label="Travaux de couverture")


def test_filiales_sur_le_site_de_leur_groupe_rejetees():
    mallet_legal = html(title="Mentions légales - Groupe Mallet", h1="Mentions légales", canonical=False,
                        body="<p>Éditeur du site : GROUPE MALLET SAS au capital de 100 000 €, RCS Troyes, siège 1 rue du Port 10000 Troyes.</p>")
    mallet = html(title="Groupe Mallet - Ingénierie et maintenance industrielle", h1="Groupe Mallet", canonical=False,
                  body='<header><a href="/groupe">Le groupe</a> <a href="/filiales">Nos filiales</a></header>'
                       "<p>Le Groupe Mallet réunit plusieurs sociétés : Omtek, Egdi, Mallet Industrie. Nos implantations : Troyes (10000), "
                       "La Chapelle-Saint-Luc (10600), Reims (51100), Dijon (21000).</p>"
                       '<a href="/mentions-legales">Mentions légales</a>')
    trema = html(title="Trema Multitechnique - Multitechnique du bâtiment", h1="Trema Multitechnique", canonical=False,
                 body="<p>Électricité, plomberie, couverture : Trema Multitechnique intervient à Troyes et dans toute l'Aube. "
                      "Nos filiales : Secabat (couverture), Elec 10.</p><footer>© 2023 Trema Multitechnique · Mentions légales</footer>")
    web = FakeWeb().add_site("groupe-mallet.fr", mallet, legal=mallet_legal).add_site("trema-multitechnique.com", trema)
    for company, url in ((OMTEK, "https://groupe-mallet.fr/"), (SECABAT, "https://trema-multitechnique.com/")):
        res, verdict = _google(company, url, web)
        assert not res.accepted and verdict in ("rejected:other_company", "rejected:group_site"), (company["company_name"], verdict)
        assert sf.check_site(company, url, web)[0] in ("other_company", "group_site")


# ── 4. Electri'Zon : page « Site en Maintenance », certificat de l'hébergeur ───────────────────────────────────
ELECTRIZON = dict(siret="81234567800017", siren="812345674", company_name="ELECTRI'ZON", city="TROYES", postal_code="10000",
                  address="20 RUE DE LA PAIX 10000 TROYES", phone="0611223344", activity_label="Travaux d'installation électrique")


def test_page_site_en_maintenance_rejetee():
    page = html(title="Electri'Zon", h1="Site en Maintenance", canonical=False,
                body="<p>Notre site est en cours de maintenance. Nous serons bientôt de retour. Merci de votre patience.</p>")
    web = FakeWeb().add_site("www.electrizon.fr", page, https=False)
    res, verdict = _google(ELECTRIZON, "http://www.electrizon.fr/", web)
    assert not res.accepted and verdict == "rejected:maintenance" and res.status == "UNCERTAIN"    # le bon nom : à trancher, pas de refonte
    assert sf.check_site(ELECTRIZON, "http://www.electrizon.fr/", web)[0] == "maintenance"


def test_certificat_https_invalide_accepte_par_defaut():
    """Par défaut (LOCAL_SITE_SSL_STRICT=0), un vieux site http au https invalide reste un vrai site : bon prospect de refonte."""
    page = html(title="Electri'Zon - Électricien à Troyes", h1="Electri'Zon", canonical=False,
                body="<p>Installation électrique, dépannage, mise aux normes à Troyes et alentours. Tél. 06 11 22 33 44</p>")
    web = SslWeb(bad_tls={"www.electrizon.fr"}).add_site("www.electrizon.fr", page, https=False)
    assert _google(ELECTRIZON, "http://www.electrizon.fr/", web)[0].accepted


def test_certificat_https_invalide_pas_un_site_exploitable(monkeypatch):
    monkeypatch.setattr(sf, "SSL_STRICT", True)
    page = html(title="Electri'Zon - Électricien à Troyes", h1="Electri'Zon", canonical=False,
                body="<p>Installation électrique, dépannage, mise aux normes à Troyes et alentours. Tél. 06 11 22 33 44</p>")
    web = SslWeb(bad_tls={"www.electrizon.fr"}).add_site("www.electrizon.fr", page, https=False)
    res, verdict = _google(ELECTRIZON, "http://www.electrizon.fr/", web)
    assert not res.accepted and res.status == "UNCERTAIN" and verdict == "rejected:maintenance"
    assert sf.check_site(ELECTRIZON, "http://www.electrizon.fr/", web)[0] == "ssl"
    assert sf.check_site(ELECTRIZON, "https://www.electrizon.fr/", web)[0] == "ssl"                # lu en https : même verdict
    ok = SslWeb().add_site("www.electrizon.fr", page, https=False)                                 # pas de https du tout : rien de conclu
    assert _google(ELECTRIZON, "http://www.electrizon.fr/", ok)[0].accepted


# ── Ce qui doit rester accepté ──────────────────────────────────────────────────────────────────────────────────
def test_vrai_site_de_tpe_nom_et_ville_accepte():
    web = FakeWeb().add_site("www.boulangerie-martin-troyes.fr", html(body="<p>Pains et viennoiseries au levain, 12 rue Émile Zola à Troyes.</p>"))
    res, verdict = _google(MARTIN, "https://www.boulangerie-martin-troyes.fr/", web)
    assert res.accepted and verdict in ("confirmed", "probable")


DUPONT_CHAUFFAGE = dict(siret="60123456700011", siren="601234567", company_name="DUPONT CHAUFFAGE", city="TROYES", postal_code="10000",
                        address="4 RUE VOLTAIRE 10000 TROYES", phone="0325445566", activity_label="Travaux d'installation de chauffage")


def test_chauffagiste_qui_fait_de_la_maintenance_n_est_pas_une_page_de_maintenance():
    page = html(title="Dupont Chauffage - Installation et maintenance de chaudières à Troyes", h1="Entretien et maintenance de chaudières",
                canonical=False, body="<p>Contrat de maintenance annuel, dépannage rapide. Spécialiste en maintenance de pompes à chaleur. "
                                      "Votre chaudière en maintenance chaque année : obligatoire. Troyes 10000 - 03 25 44 55 66</p>")
    assert not sf.looks_maintenance(sf.htmlinfo.parse(page, "https://www.dupont-chauffage.fr/"))   # page courte, mais un MÉTIER
    web = FakeWeb().add_site("www.dupont-chauffage.fr", page)
    res, verdict = _google(DUPONT_CHAUFFAGE, "https://www.dupont-chauffage.fr/", web)
    assert res.accepted and verdict in ("confirmed", "probable")
    assert sf.check_site(DUPONT_CHAUFFAGE, "https://www.dupont-chauffage.fr/", web)[0] == "ok"


def test_pages_de_maintenance_types_et_faux_positifs():
    def maint(title, body, links=0):
        nav = "".join(f'<a href="/p{i}">Page {i}</a>' for i in range(links))
        return sf.looks_maintenance(sf.htmlinfo.parse(html(title=title, h1="", body=nav + body), "https://www.exemple.fr/"))
    assert maint("Maintenance", "<p>Revenez plus tard.</p>")
    assert maint("Exemple", "<p>Site en maintenance, bientôt de retour !</p>")
    assert maint("Exemple", "<p>We'll be back soon.</p>")
    assert maint("Compte suspendu", "<p>Ce compte d'hébergement a été suspendu.</p>")
    assert not maint("Exemple - Maintenance industrielle", "<p>Spécialiste en maintenance industrielle à Troyes.</p>")
    assert not maint("Exemple", "<p>Site en maintenance ? Nous dépannons aussi les sites internet de nos clients.</p>" + "<p>Contenu.</p>" * 200, links=12)


def test_editeur_ou_copyright_d_une_holding_au_meme_nom_n_est_pas_une_autre_entreprise():
    assert not sf.other_name(MARTIN, "HOLDING MARTIN SAS")
    assert not sf.other_name(MARTIN, "Tous droits réservés")
    assert sf.other_name(AUTO10, "Fuhrmann Espaces Verts")


def test_version_de_la_regle_identique_cote_web():
    import pathlib
    import re
    ts = (pathlib.Path(__file__).resolve().parents[2] / "web" / "lib" / "localdb.ts").read_text(encoding="utf-8")
    assert int(re.search(r"IDENTITY_VERSION = (\d+)", ts).group(1)) == sf.IDENTITY_VERSION       # « encore à relire » (Statistiques)
