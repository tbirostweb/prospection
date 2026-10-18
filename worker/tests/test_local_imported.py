"""Fiches importées : vérification du site déclaré (sans moteur), âge du site (« © ») et règle « site récent = écarté »."""
from datetime import date

from worker.local import audit, imported, scoring
from worker.local.sitefinder import Thresholds

from local_fixtures import LEGAL_MARTIN, MARTIN, FakeWeb, html

SITE = "https://www.boulangerie-martin.fr/"


def martin(web: FakeWeb, home: str | None = None, legal: str | None = LEGAL_MARTIN) -> FakeWeb:
    home = home or html(body='<a href="/mentions-legales">Mentions légales</a>')
    return web.add_site("www.boulangerie-martin.fr", home, legal=legal)


# ── année du site ─────────────────────────────────────────────────────────────────────────────────────────
VP = '<meta name="viewport" content="width=device-width">'


def test_annee_du_site_mention_simple_et_intervalle():
    now = date(2026, 10, 18)
    assert audit.site_year(VP + "<footer>© 2025 Boulangerie</footer>", now) == 2025
    assert audit.site_year(VP + "<footer>&copy; 2012 - 2026 Boulangerie</footer>", now) == 2012    # « depuis 2012 » : site ancien
    assert audit.site_year(VP + "<footer>Copyright 2019–2026</footer>", now) == 2019               # tiret long accepté
    assert audit.site_year(VP + "<p>Ouvert depuis 2025</p>", now) is None                          # pas une mention « © »
    assert audit.site_year(VP + "<footer>© 2031</footer>", now) is None                            # année future ignorée


def test_annee_en_cours_dementie_par_un_site_techniquement_ancien():
    """Pied de page « © année en cours » automatique sur un vieux site : jamais pris pour un site récent."""
    now = date(2026, 10, 18)
    assert audit.site_year("<footer>© 2026</footer>", now) is None                                 # pas de viewport mobile
    assert audit.site_year(VP + "<center>Bienvenue</center><footer>© 2026</footer>", now) is None  # balise obsolète
    assert audit.site_year(VP + '<script src="/js/jquery-1.7.2.min.js"></script><footer>© 2026</footer>', now) is None


# ── site récent écarté par le score ───────────────────────────────────────────────────────────────────────
def _scored(**kw):
    p = dict(website_status="CONFIRMED", phone="0325123456", email="contact@boulangerie-martin.fr", activity_key="artisan", distance_km=2.0, **kw)
    return scoring.score_prospect(p, today=date(2026, 10, 18))


def test_site_recent_ecarte_meme_avec_un_email():
    s = _scored(site_year=scoring.RECENT_SITE_YEAR)
    assert s["category"] == "IGNORER" and s["score"] == 0
    assert any("site récent" in c["reason"] for c in s["caps"])


def test_site_ancien_ou_annee_inconnue_non_ecarte_par_cette_regle():
    for year in (scoring.RECENT_SITE_YEAR - 1, None):
        s = _scored(site_year=year)
        assert not any("site récent" in c["reason"] for c in s["caps"])


def test_annee_recente_sans_site_retenu_ne_compte_pas():
    s = scoring.score_prospect(dict(website_status="UNCERTAIN", site_year=2025, phone="0325123456"), today=date(2026, 10, 18))
    assert not any("site récent" in c["reason"] for c in s["caps"])


# ── vérification du site déclaré ──────────────────────────────────────────────────────────────────────────
def test_site_declare_confirme_par_siret_sur_les_mentions_legales():
    r = imported.verify_site(MARTIN, SITE, martin(FakeWeb()), email="contact@boulangerie-martin.fr")
    assert r["status"] == "CONFIRMED" and r["confidence"] >= 0.9
    codes = {e["code"] for e in r["evidence"]}
    assert {"siret", "email_on_site", "email_domain"} <= codes


def test_email_sur_le_domaine_du_site_compte_mais_jamais_une_messagerie_gratuite():
    web = martin(FakeWeb(), legal=None)
    pro = imported.verify_site(MARTIN, SITE, web, email="bonjour@boulangerie-martin.fr")
    free = imported.verify_site(MARTIN, SITE, web, email="boulangerie.martin@gmail.com")
    assert "email_domain" in {e["code"] for e in pro["evidence"]}
    assert "email_domain" not in {e["code"] for e in free["evidence"]}
    assert pro["confidence"] > free["confidence"]


def test_reseau_social_de_la_fiche_lie_depuis_le_site():
    home = html(body='<a href="/mentions-legales">Mentions légales</a><a href="https://www.facebook.com/boulangeriemartin/">Facebook</a>')
    r = imported.verify_site(MARTIN, SITE, martin(FakeWeb(), home=home, legal=None),
                             socials=[{"network": "Facebook", "url": "https://facebook.com/boulangeriemartin"}])
    assert "social_link" in {e["code"] for e in r["evidence"]}


def test_site_sans_rapport_reste_a_verifier():
    web = FakeWeb().add_site("www.autre-chose.fr", html(title="Garage Dupont - Lyon", h1="Garage Dupont", desc="Mécanique à Lyon"))
    r = imported.verify_site(MARTIN, "https://www.autre-chose.fr/", web, email="contact@boulangerie-martin.fr")
    assert r["status"] == "UNCERTAIN"


def test_site_injoignable():
    r = imported.verify_site(MARTIN, "https://www.introuvable.fr/", FakeWeb())
    assert r["status"] == "UNREACHABLE" and not r["reachable"]


def test_seuil_appris_plus_bas_accepte_en_probable():
    web = martin(FakeWeb(), legal=None)
    base = imported.verify_site(MARTIN, SITE, web, email="bonjour@boulangerie-martin.fr", thresholds=Thresholds())
    assert base["confidence"] < Thresholds().probable
    learned = imported.verify_site(MARTIN, SITE, web, email="bonjour@boulangerie-martin.fr", thresholds=Thresholds(), learned=base["confidence"])
    assert learned["status"] == "PROBABLE"


def test_normalisation_des_profils_sociaux():
    assert imported.socials_mod_norm("https://www.facebook.com/x/?ref=1") == imported.socials_mod_norm("http://facebook.com/x")
