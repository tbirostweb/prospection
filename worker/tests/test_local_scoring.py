"""Scoring local explicable, catégories, exclusions, chaînes, Lighthouse = signal, poids configurables."""
from datetime import date

import pytest

from worker.local import naf, scoring

TODAY = date(2026, 9, 21)
BASE = dict(siret="12345678900011", distance_km=3.0, company_created_at=date(2020, 1, 1), employee_range="NN", website_status="NOT_FOUND", website_absence_confidence=0.85)


def score(**over):
    web = over.pop("activity_web", 0.9)
    return scoring.score_prospect({**BASE, **over}, activity_web=web, radius_km=20, today=TODAY)


def pts(s, key):
    return next(d["points"] for d in s["details"] if d["key"] == key)


def test_les_poids_font_100_et_sont_configurables():
    assert sum(scoring.DEFAULT_WEIGHTS.values()) == 100
    s = scoring.score_prospect({**BASE}, {"site_potential": 10, "bidule": 99}, None, 0.9, 20, TODAY)      # clé inconnue ignorée
    assert pts(s, "site_potential") == 10.0
    assert scoring.merge_weights({"proximity": "beaucoup"})["proximity"] == 10                             # valeur invalide ignorée


def test_entreprise_sans_site_recente_proche_avec_contact_est_a_contacter_jamais_tres_bon():
    """« Site non trouvé » n'est pas une certitude : score de base plafonné à 74, jamais 🔥 sur ce seul signal (constaté sur données réelles).
    L'ouverture récente est un signal d'achat : il départage au-dessus du plafond de base, sans atteindre « Très bon »."""
    s = score(company_created_at=date(2026, 6, 1), phone="0325123456", email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9, distance_km=2.0)
    assert s["category"] == "A_CONTACTER" and scoring.NO_SITE_CAP < s["score"] < scoring.DEFAULT_THRESHOLDS["TRES_BON"]
    assert any("jamais « Très bon »" in d["detail"] for d in s["details"] if d["key"] == "cap")
    assert pts(s, "site_potential") == 30.0 and s["stage"] == "FINAL"
    assert any("aucun site officiel identifié" in d["detail"] and "ne prouve pas" in d["detail"] for d in s["details"])   # formulation prudente


def test_score_explicable_la_somme_des_points_egale_le_score():
    s = score(phone="0325123456", activity_web=0.3, contact_confidence=0.9)
    assert abs(sum(d["points"] for d in s["details"] if d["key"] != "cap") - s["score"]) <= 1
    assert all(d["label"] and d["detail"] for d in s["details"]) and s["summary"]


def test_site_moderne_et_performant_est_faible_priorite():
    s = score(website_status="CONFIRMED", website_confidence=0.9, audited_at=TODAY, modernization_opportunity="LOW", technical_score=98,
              seo_score=95, seo_opportunity_score=5, performance_score=100, company_created_at=date(2010, 1, 1), distance_km=15)
    assert s["score"] < 45 and s["category"] in ("FAIBLE", "IGNORER") and pts(s, "site_potential") <= 5


def test_site_a_moderniser_avec_seo_incomplet_et_contact_est_a_contacter():
    s = score(website_status="CONFIRMED", website_confidence=0.95, audited_at=TODAY, modernization_opportunity="HIGH", technical_score=40,
              seo_score=35, seo_opportunity_score=65, performance_score=30, email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9, distance_km=4)
    assert s["category"] in ("A_CONTACTER", "TRES_BON") and pts(s, "site_potential") >= 19 and pts(s, "seo_technical") >= 10


def test_site_inaccessible_incertain_et_non_verifie_sont_traites_differemment():
    unreachable = score(website_status="UNREACHABLE")
    uncertain = score(website_status="UNCERTAIN")
    assert pts(unreachable, "site_potential") == 25.0 and pts(uncertain, "site_potential") < 12
    assert unreachable["score"] > uncertain["score"]                        # l'incertain n'est jamais traité comme « pas de site »


def test_lighthouse_est_un_signal_jamais_une_raison_suffisante():
    good = dict(website_status="CONFIRMED", website_confidence=0.95, audited_at=TODAY, modernization_opportunity="LOW", technical_score=97,
                seo_score=92, seo_opportunity_score=8, performance_score=95, company_created_at=date(2012, 1, 1), distance_km=12)
    without = score(**good)
    with_bad_lighthouse = score(**good, lighthouse_performance=10)
    assert with_bad_lighthouse["category"] not in ("TRES_BON", "A_CONTACTER")   # un mauvais Lighthouse ne fait pas un bon prospect à lui seul
    assert with_bad_lighthouse["score"] - without["score"] <= 6


def test_chaine_franchise_plafonnee_meme_avec_un_site_mediocre():
    s = score(is_chain=True, website_status="UNREACHABLE", distance_km=1, email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9)
    assert s["score"] <= scoring.CHAIN_CAP and s["category"] in ("A_EXAMINER", "FAIBLE", "IGNORER")
    assert any("chaîne" in d["detail"] for d in s["details"])
    independent = score(website_status="UNREACHABLE", distance_km=1, email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9)
    assert independent["score"] > s["score"] + 20


def test_agence_web_exclue_et_ne_plus_contacter_toujours_ignorer():
    assert score(excluded_reason="activité NAF 62.01Z")["category"] == "IGNORER" and score(excluded_reason="x")["score"] == 0
    dnc = score(do_not_contact=1, email="contact@x.fr", email_kind="GENERIC_BUSINESS", contact_confidence=0.9)
    assert dnc["score"] == 0 and dnc["category"] == "IGNORER"
    assert score(status="DO_NOT_CONTACT")["category"] == "IGNORER"


def test_score_preliminaire_plafonne_et_sans_categorie():
    s = score(website_status=None, distance_km=1, company_created_at=date(2026, 8, 1))
    assert s["stage"] == "PRELIMINARY" and s["category"] is None and s["score"] <= scoring.PRELIMINARY_CAP


def test_proximite_ameliore_le_score_sans_etre_obligatoire():
    near, far, unknown = (score(distance_km=d, phone="0325123456", activity_web=0.3, contact_confidence=0.9) for d in (1, 28, None))
    assert near["score"] > far["score"] > 0 and pts(unknown, "proximity") == 4.0           # inconnue : neutre
    assert far["score"] >= 40                                                              # un prospect éloigné reste un prospect


def test_nouvelle_entreprise_bonus_borne_et_bodacc_confirme():
    old = score(company_created_at=date(2015, 1, 1))
    new = score(company_created_at=date(2026, 7, 1))
    confirmed = score(company_created_at=date(2025, 12, 1), bodacc={"kind": "creation", "published": "2026-01-10"})
    assert pts(old, "freshness") == 0 and pts(new, "freshness") == 5.0 and pts(confirmed, "freshness") > 0
    assert pts(new, "freshness") <= scoring.DEFAULT_WEIGHTS["freshness"]


def test_seuils_configurables():
    s = scoring.score_prospect({**BASE}, None, {"A_CONTACTER": 99, "TRES_BON": 100, "A_EXAMINER": 50}, 0.9, 20, TODAY)
    assert s["category"] in ("A_EXAMINER", "FAIBLE")


# ── activités, exclusions, chaînes ──
def test_activites_configurables_et_codes_naf_bruts():
    codes, unknown = naf.resolve_activities(["restaurants", "boulangeries", "96.09Z", "n'importe quoi"])
    assert "56.10A" in codes and "10.71C" in codes and "96.09Z" in codes and unknown == ["n'importe quoi"]
    cat = naf.load_catalog({"tatoueurs": {"label": "Tatoueurs", "naf": ["96.09Z"], "web": 0.9}})
    assert naf.resolve_activities(["tatoueurs"], cat)[0] == ["96.09Z"] and naf.activity_of("96.09Z", cat)[1] == "Tatoueurs"


@pytest.mark.parametrize("naf_code,name,excluded", [
    ("62.01Z", "DEV SOLUTIONS", True), ("73.11Z", "PUB & CO", True), ("56.10A", "AGENCE WEB DU CENTRE", True),
    ("56.10A", "STUDIO WEB TROYES", True), ("56.10A", "LE BISTROT DU COIN", False), ("47.76Z", "FLEURS DE MARIE", False)])
def test_exclusions_web_et_marketing(naf_code, name, excluded):
    assert bool(naf.excluded_reason(naf_code, name)) is excluded


@pytest.mark.parametrize("name,trade,size,emp,open_,chain", [
    ("BURGER KING FRANCE", None, "GE", None, None, True), ("SPINACH MFCO", "POPEYES FAMOUS LOUISIANA", "ETI", None, None, True),
    ("SARL DUPONT", "Boulangerie Paul", None, None, None, True), ("LE BISTROT DU COIN", None, "PME", "02", 1, False),
    ("SAS MARTIN", "Franck Provost", "PME", "02", 1, True), ("SARL LEROY", "Basic-Fit", "PME", "01", 1, True), ("CHEZ JULES", None, "PME", "01", 1, False),
    ("GARAGE MARTIN", None, "PME", "22", 2, True), ("PIZZERIA ROMA", None, "PME", "03", 7, True)])
def test_chaines_franchises_et_grosses_entreprises(name, trade, size, emp, open_, chain):
    assert bool(naf.chain_signals(name, trade, size, emp, open_)) is chain


# ── cohérence web ↔ worker (le web duplique les constantes du barème local et le catalogue d'activités) ──
import re  # noqa: E402
from pathlib import Path  # noqa: E402

WEB = Path(__file__).resolve().parents[2] / "web"


def _ts_object(source: str, name: str) -> dict:
    m = re.search(rf"\b{name}\s*=\s*\{{([^}}]*)\}}", source)
    assert m, f"{name} introuvable"
    return {k: float(v) for k, v in re.findall(r"(\w+):\s*(-?[\d.]+)", m.group(1))}


def test_constantes_du_web_identiques_au_worker():
    ts = (WEB / "lib/local.ts").read_text(encoding="utf-8")
    assert _ts_object(ts, "LOCAL_WEIGHTS") == {k: float(v) for k, v in scoring.DEFAULT_WEIGHTS.items()}
    assert _ts_object(ts, "LOCAL_THRESHOLDS") == {k: float(v) for k, v in scoring.DEFAULT_THRESHOLDS.items()}
    labels = re.search(r"LOCAL_WEIGHT_LABELS[^=]*=\s*\{(.*?)\n\};", ts, re.S).group(1)
    assert set(re.findall(r"(\w+):", labels)) >= set(scoring.DEFAULT_WEIGHTS)


def test_catalogue_d_activites_du_web_identique_au_worker():
    ts = (WEB / "lib/local.ts").read_text(encoding="utf-8")
    block = re.search(r"LOCAL_ACTIVITIES[^=]*=\s*\[(.*?)\n\];", ts, re.S).group(1)
    web_keys = re.findall(r'\["(\w+)",', block)
    assert set(web_keys) == set(naf.CATALOG)                                   # aucune activité proposée que le worker ne connaît pas


def test_categories_du_web_couvrent_celles_du_worker():
    ts = (WEB / "lib/local.ts").read_text(encoding="utf-8")
    block = re.search(r"CATEGORY_LABELS[^=]*=\s*\{(.*?)\n\};", ts, re.S).group(1)
    assert set(re.findall(r"(\w+):", block)) == set(scoring.CATEGORY_LABELS)


def test_sans_contact_public_le_score_est_plafonne():
    s = score(website_status="UNCERTAIN", activity_web=1.0)
    with_contact = score(website_status="UNCERTAIN", activity_web=1.0, phone="0325123456")
    assert s["score"] <= scoring.NO_CONTACT_CAP and any("aucun contact" in d["detail"] for d in s["details"] if d["key"] == "cap")
    assert with_contact["score"] >= s["score"]


@pytest.mark.parametrize("trade,naf_code,banned", [
    ("Franck Provost", "96.02A", True), ("Basic-Fit Troyes", "93.13Z", True), ("Optic 2000", "47.78A", True), ("Century 21 Agence du Centre", "68.31Z", True),
    ("Speedy", "45.20A", True), ("PAUL", "10.71C", True), ("Chez Paul", "56.10A", False), ("Garage Paul", "45.20A", False),
    ("Orange", "47.42Z", True), ("Le Jardin d'Orange", "56.10A", False), ("Carrefour City", "47.11B", True), ("Au Carrefour des Saveurs", "56.10A", False),
    ("Boulangerie Dupont", "10.71C", False), ("La Criée", "56.10A", False), ("Nicolas", "47.25Z", True), ("Nicolas Coiffure", "96.02A", False)])
def test_liste_des_enseignes_bannies(trade, naf_code, banned):
    assert bool(naf.chain_signals("SARL X", trade, "PME", "01", 1, naf=naf_code)) is banned


def test_enseignes_bannies_ajoutees_dans_les_reglages():
    try:
        assert not naf.chain_signals("SARL X", "Pizza Mamma Mia", "PME", "01", 1, naf="56.10C")
        naf.set_extra_brands(["Pizza Mamma Mia", "x"])                           # « x » : trop court, ignoré
        assert naf.chain_signals("SARL X", "Pizza Mamma Mia Troyes", "PME", "01", 1, naf="56.10C")
        assert not naf.chain_signals("SARL X", "Boulangerie X", "PME", "01", 1, naf="10.71C")
    finally:
        naf.set_extra_brands([])


def test_activites_favorisees_et_bruitees_identiques_web_et_worker():
    ts = (WEB / "lib/local.ts").read_text(encoding="utf-8")
    for const, expected in (("FAVORED_ACTIVITIES", naf.FAVORED), ("NOISY_ACTIVITIES", naf.NOISY)):
        block = re.search(rf"{const}\s*=\s*\[(.*?)\];", ts, re.S).group(1)
        assert set(re.findall(r'"(\w+)"', block)) == expected
    from worker.local import signals
    assert f"ABSENCE_MIN = {signals.ABSENCE_MIN}" in ts
