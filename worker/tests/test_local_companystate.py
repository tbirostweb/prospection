"""État officiel d'une entreprise (worker/local/companystate.py) : verdict d'après SIRENE et BODACC, sans réseau."""
import datetime as dt

from worker.local import companystate as cs

TODAY = dt.date(2026, 10, 28)
ACTIVE = {"unit": "A", "unit_closed_at": None, "etab": "A", "etab_closed_at": None, "etab_found": True, "active_elsewhere": []}


def ev(kind, nature, date):
    return {"kind": kind, "nature": nature, "date": date, "published": date}


def test_liquidation_judiciaire_datee():
    v = cs.verdict(ACTIVE, [ev("collective", "Jugement d'ouverture de liquidation judiciaire", "2026-03-17")], "2015-01-01", TODAY)
    assert v == {"closed": True, "reason": "liquidation judiciaire (17/03/2026)", "source": "bodacc"}


def test_redressement_sauvegarde_et_fin_de_procedure():
    assert cs.verdict(ACTIVE, [ev("collective", "Jugement d'ouverture d'une procédure de redressement judiciaire", "2026-01-10")], None, TODAY)["reason"] \
        == "redressement judiciaire (10/01/2026)"
    assert cs.verdict(ACTIVE, [ev("collective", "Jugement d'ouverture d'une procédure de sauvegarde", "2025-05-02")], None, TODAY)["reason"] \
        == "procédure de sauvegarde (02/05/2025)"
    done = [ev("collective", "Jugement d'ouverture d'une procédure de sauvegarde", "2025-05-02"),
            ev("collective", "Jugement mettant fin à la procédure de sauvegarde", "2026-02-01")]
    assert cs.verdict(ACTIVE, done, None, TODAY)["closed"] is False
    old = [ev("collective", "Jugement d'ouverture d'une procédure de redressement judiciaire", "2019-01-10")]
    assert cs.verdict(ACTIVE, old, None, TODAY)["closed"] is False                    # plus de 3 ans : plus « en procédure »
    conv = [ev("collective", "Jugement d'ouverture d'une procédure de redressement judiciaire", "2025-11-01"),
            ev("collective", "Jugement de conversion en liquidation judiciaire", "2026-03-17"),
            ev("collective", "Jugement de clôture pour insuffisance d'actif", "2026-09-01")]
    assert cs.verdict(ACTIVE, conv, None, TODAY)["reason"] == "liquidation judiciaire (17/03/2026)"


def test_etablissement_ferme_et_transfert_signale_sans_rien_inventer():
    view = {**ACTIVE, "etab": "F", "etab_closed_at": "2026-09-01", "active_elsewhere": [{"siret": "11111111100029", "address": "3 RUE DE LA PAIX"}]}
    v = cs.verdict(view, [], None, TODAY)
    assert v["closed"] and v["source"] == "sirene" and v["reason"].startswith("établissement fermé (01/09/2026) ; autre établissement actif")
    assert cs.verdict({**view, "active_elsewhere": []}, [], None, TODAY)["reason"] == "établissement fermé (01/09/2026)"


def test_unite_legale_cessee_radiation_et_dissolution():
    assert cs.verdict({**ACTIVE, "unit": "C", "unit_closed_at": "2026-06-30"}, [], None, TODAY)["reason"] == "entreprise cessée (30/06/2026)"
    assert cs.verdict(ACTIVE, [ev("radiation", "radiation du RCS", "2026-04-02")], "2010-01-01", TODAY)["reason"] == "radiée du RCS (02/04/2026)"
    moved = [ev("radiation", "radiation du RCS", "2026-04-02"), ev("creation", "immatriculation", "2026-04-10")]
    assert cs.verdict(ACTIVE, moved, "2010-01-01", TODAY)["closed"] is False         # radiée d'un greffe, immatriculée ailleurs : transfert
    assert cs.verdict(ACTIVE, [ev("radiation", "radiation du RCS", "2026-04-02")], "2026-05-01", TODAY)["closed"] is False   # recréée depuis
    assert cs.verdict(ACTIVE, [ev("company_end", "dissolution", "2026-08-01")], None, TODAY)["reason"] == "dissolution (01/08/2026)"


def test_entreprise_active_ou_inconnue_intacte():
    assert cs.verdict(ACTIVE, [], "2020-01-01", TODAY) == {"closed": False, "reason": None, "source": None}
    assert cs.verdict(None, [], None, TODAY)["closed"] is False                       # SIRENE ne la trouve pas : rien de conclu


def test_vue_sirene_de_notre_etablissement():
    unit = {"siren": "111111111", "etat_administratif": "A", "matching_etablissements": [{"siret": "11111111100011", "etat_administratif": "F",
            "date_fermeture": "2026-09-01"}], "siege": {"siret": "11111111100029", "etat_administratif": "A", "adresse": "3 RUE DE LA PAIX 10000 TROYES"}}
    v = cs.sirene_view(unit, "11111111100011")
    assert (v["etab"], v["etab_closed_at"], v["active_elsewhere"][0]["siret"]) == ("F", "2026-09-01", "11111111100029")
    assert cs.sirene_view(unit, "11111111100099")["etab"] is None                      # établissement absent de la réponse : inconnu
    assert cs.sirene_view(None, "11111111100011") is None


def test_envoi_fiche_deja_ecartee_sans_requete():
    assert cs.send_check(None, {"id": 1, "excluded_reason": "liquidation judiciaire (17/03/2026)"}, dt.datetime.now(dt.timezone.utc)) \
        == ("closed", "liquidation judiciaire (17/03/2026)")
    fresh = {"company_state": {"checked_at": "2026-10-27T10:00:00Z", "version": cs.VERSION, "closed": False}}
    assert cs.send_check(None, {"id": 1, "siren": "123456789", "provenance": fresh}, dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc)) == ("ok", "")
