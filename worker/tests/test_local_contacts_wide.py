"""Contacts élargis : e-mails d'un autre domaine (UNCERTAIN, hors agence web), sites « à trancher » (attribution), relecture des sites sans e-mail."""
from __future__ import annotations

from types import SimpleNamespace

from worker.local import contacts, enrich, htmlinfo, runner

SITE = "boulangerie-martin.fr"
COMPANY = {"company_name": "BOULANGERIE MARTIN", "trade_name": None, "city": "Troyes", "postal_code": "10000", "department": "10"}


def _page(body, url=f"https://www.{SITE}/", head=""):
    return htmlinfo.parse(f"<html><head>{head}</head><body>{body}</body></html>", url)


def test_email_d_un_autre_domaine_garde_uncertain():
    c = contacts.extract([_page("<p>Écrivez-nous : boulangerie.martin@mon-courrier.fr</p>")], SITE)
    assert c["email"] == "boulangerie.martin@mon-courrier.fr" and c["email_kind"] == "UNCERTAIN"


def test_email_du_domaine_du_site_prefere_a_un_autre_domaine():
    c = contacts.extract([_page("<p>x@mon-courrier.fr</p><p>jean.martin@boulangerie-martin.fr</p>")], SITE)
    assert c["email"] == "jean.martin@boulangerie-martin.fr"
    c = contacts.extract([_page("<p>contact@mon-courrier.fr</p><p>martin2012x@boulangerie-martin.fr</p>")], SITE)
    assert c["email"] == "martin2012x@boulangerie-martin.fr"                      # même UNCERTAIN sur le domaine du site avant l'autre domaine


def test_email_agence_web_ou_hebergeur_ecarte():
    p = _page("<p>Tél 03 25 12 34 56</p><footer>Site réalisé par Pixel Agency - studio@pixel-agency.fr</footer>")
    assert contacts.extract([p], SITE)["email"] is None
    p = _page('<footer>© Boulangerie Martin - <a href="https://www.webcrea.io">Webcrea</a> conception</footer><p>hello@webcrea.io</p>')
    assert contacts.extract([p], SITE)["email"] is None
    p = _page("<p>Hébergé par OVH, contact : support@ovh-hosting.net</p>")
    assert contacts.extract([p], SITE)["email"] is None
    p = _page("<p>Plateforme : bob@wixpress.com noreply@boulangerie-martin.fr</p>")
    assert contacts.extract([p], SITE)["email"] is None                           # ignorés (techniques)


def test_site_a_trancher_avec_attribution():
    page = _page("<h1>Boulangerie Martin</h1><p>12 rue Thiers 10000 Troyes</p><p>contact@martin-pains.fr</p>", "https://www.martin-pains.fr/")
    c = contacts.extract_unverified([page], "martin-pains.fr", COMPANY)
    assert c["email"] == "contact@martin-pains.fr" and c["email_kind"] == "UNCERTAIN" and c["phone"] is None
    assert c["evidence"][0]["site_verified"] is False


def test_site_a_trancher_par_le_domaine_sans_ville():
    page = _page("<p>Bienvenue</p><p>contact@boulangerie-martin.fr</p>")
    assert contacts.extract_unverified([page], SITE, COMPANY)["email"] == "contact@boulangerie-martin.fr"


def test_site_a_trancher_sans_attribution_rien():
    page = _page("<h1>Pain frais</h1><p>Lyon 69001</p><p>contact@pain-frais.fr</p>", "https://www.pain-frais.fr/")
    assert contacts.extract_unverified([page], "pain-frais.fr", COMPANY)["email"] is None
    nom_sans_ville = _page("<h1>Boulangerie Martin</h1><p>contact@pain-frais.fr</p>", "https://www.pain-frais.fr/")
    assert contacts.extract_unverified([nom_sans_ville], "pain-frais.fr", COMPANY)["email"] is None


def test_candidat_refuse_autre_entreprise_jamais_lu(monkeypatch):
    monkeypatch.setattr(runner.store, "bad_domains", lambda *a: ["mauvais.fr"])
    row = {"website_status": "UNCERTAIN"}
    cand = lambda **k: SimpleNamespace(candidates=[{"url": "https://x.fr", "domain": "x.fr", "reachable": True, **k}], status="UNCERTAIN")  # noqa: E731
    assert runner._unverified_candidate(None, row, cand()) == "https://x.fr"
    assert runner._unverified_candidate(None, row, cand(verdict="no_proof")) == "https://x.fr"
    for bad in ({"verdict": "other_company"}, {"verdict": "group_site"}, {"parked": True}, {"verdict": "maintenance"}, {"domain": "mauvais.fr"}, {"reachable": False}):
        assert runner._unverified_candidate(None, row, cand(**bad)) is None, bad


def test_relecture_deep_cherche_contact_et_mentions():
    home = _page("<p>Bienvenue</p>")
    asked = []

    def fetch(url):
        asked.append(url)
        if url.endswith("/contact"):
            return SimpleNamespace(ok=True, text="<html><body><a href='mailto:contact@boulangerie-martin.fr'>m</a></body></html>", url=url)
        return None
    assert len(contacts.discover([home], fetch, SITE)) == 1                          # lecture normale : aucun lien, rien de plus
    got = contacts.discover([home], fetch, SITE, deep=True)
    assert contacts.extract(got, SITE)["email"] == "contact@boulangerie-martin.fr" and f"https://www.{SITE}/contact" in asked


def test_relecture_enrich_compteur_et_delai(monkeypatch):
    seen = {}

    class Conn:
        pass
    monkeypatch.setattr(enrich, "_rows", lambda conn, where, limit, order=None: seen.setdefault("where", where) and [])
    enrich.site_emails(Conn(), lambda u: None, limit=5)
    w = seen["where"]
    assert "site_email.tries" in w and "INTERVAL 14 DAY" in w and f"< {enrich.SITE_EMAIL_MAX_TRIES}" in w and "site_email.deep" in w   # jamais lu en profondeur : sans délai
