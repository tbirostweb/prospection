"""« Fiche complète » (worker/local/complete.py) : cas partagés avec web/lib/complete.ts (tests/fixtures/fiche_complete_cases.json)."""
from __future__ import annotations

import json
import pathlib

import pytest

from worker.local import complete

DATA = json.loads((pathlib.Path(__file__).parent / "fixtures" / "fiche_complete_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["name"] for c in DATA["cases"]])
def test_cas_partages(case):
    p = {**DATA["base"], **case["p"]}
    p["provenance"] = json.dumps(p["provenance"]) if p["provenance"] is not None else None    # comme lu en base
    assert complete.missing(p, case["personal"]) == case["missing"]
    assert complete.channel(p, case["personal"]) == case["channel"]
    why = complete.reason(p, case["personal"])
    # l'e-mail d'abord : le site ne bloque plus l'envoi (il oriente le message), seuls les autres manques comptent
    assert (why is None) == (complete.email_ok(p, case["personal"]) and set(case["missing"]) <= {"site"})
    assert complete.angle(p) == case["angle"]                                          # même angle que ficheAngle (web)
    assert complete.site_reason(p) == case["site_reason"]
    assert (complete.site_reason(p) is None) == ("site" not in case["missing"])        # une raison pour chaque « site à trancher »
    assert complete.contact_reason(p, case["personal"]) == case["contact_reason"]


def test_raisons_libelles_et_ordre():
    assert all(code in complete.SITE_REASONS for code in ("review", "search", "recheck", "places", "presend", "uncertain", "uncertain_user", "unreachable"))
    assert set(complete.CONTACT_REASONS) == {"invalid", "refused", "form", "none"}
    # une fiche avec un téléphone n'est jamais « sans contact », même sans e-mail
    p = {**DATA["base"], "email": None, "email_kind": None, "phone": "0325000000"}
    assert complete.contact_reason(p, False) is None and "contact" not in complete.missing(p, False)


def test_raison_lisible():
    p = {**DATA["base"], "activity_label": None}
    assert complete.reason(p, True) == "fiche incomplète : métier inconnu (activité non classée)"


BASE = {**DATA["base"], "website_url": "https://garage-exemple.fr/"}


@pytest.mark.parametrize("fields,expected", [
    ({"website_status": "CONFIRMED"}, "refonte"),
    ({"website_status": "PROBABLE"}, "refonte"),
    ({"website_status": "PROBABLE", "site_review": "PENDING"}, "neutre"),            # site importé à vérifier : pas vérifié
    ({"website_status": "UNCERTAIN", "site_review": "CONFIRMED"}, "refonte"),        # confirmé par toi
    ({"website_status": "CONFIRMED", "website_url": None}, "neutre"),
    ({"website_status": "UNCERTAIN"}, "neutre"),
    ({"website_status": "UNREACHABLE"}, "neutre"),
    ({"website_status": "UNREACHABLE", "provenance": {"unreachable_retry": {"tries": 2, "status": "UNREACHABLE"}}}, "neutre"),
    ({"website_status": None, "website_url": None}, "neutre"),                       # pas encore cherché
    ({"website_status": "NOT_FOUND", "website_url": None, "website_absence_confidence": 0.3}, "neutre"),   # recherche trop courte
    ({"website_status": "NOT_FOUND", "website_url": None, "website_absence_confidence": 0.8}, "neutre"),   # revérification pas faite
    ({"website_status": "NOT_FOUND", "website_url": None, "website_absence_confidence": 0.8,
      "provenance": {"site_recheck": {"status": "NOT_FOUND"}}}, "creation"),
    ({"website_status": "NOT_FOUND", "website_url": None, "website_absence_confidence": 0.8,
      "provenance": {"site_recheck": {"status": "NOT_FOUND"}, "places": {"had_website": True}}}, "creation"),   # vérifié juste avant l'envoi
    ({"website_status": "NOT_FOUND", "website_url": None, "website_absence_confidence": 0.8,
      "provenance": {"site_recheck": {"status": "NOT_FOUND"}, "presend_site": {"doubt": True, "at": "2026-10-01T00:00:00Z"}}}, "neutre"),
    ({"website_status": "CONFIRMED", "provenance": {"presend_identity": {"doubt": True, "url": "https://garage-exemple.fr/"}}}, "neutre"),
    ({"website_status": "CONFIRMED", "provenance": {"presend_identity": {"doubt": True, "url": "https://autre.fr/"}}}, "refonte"),
])
def test_angle_du_message(fields, expected):
    p = {**BASE, **fields}
    p["provenance"] = json.dumps(p["provenance"]) if p.get("provenance") is not None else None
    assert complete.angle(p) == expected


def test_le_site_ne_bloque_plus_un_e_mail_utilisable():
    for status in ("UNCERTAIN", "UNREACHABLE", None):
        p = {**BASE, "website_status": status}
        assert "site" in complete.missing(p, True)                 # toujours affiché et traité par les passages automatiques
        assert complete.reason(p, True) is None                    # mais l'envoi n'attend plus
    # les autres garde-fous restent bloquants
    assert complete.reason({**BASE, "website_status": "UNCERTAIN", "do_not_contact": 1}, True) is not None
    assert complete.reason({**BASE, "website_status": "UNCERTAIN", "auto_state": "bounced"}, True) is not None
    assert complete.reason({**BASE, "website_status": "UNCERTAIN", "bodacc": {"kind": "radiation"}}, True) is not None
    assert complete.reason({**BASE, "website_status": "UNCERTAIN", "excluded_reason": "fermée (SIRENE)"}, True) is not None


@pytest.mark.parametrize("kind,expected", [
    ("sans_site", "creation"), ("ai_sans_site", "creation"), ("ai_takeover_sans_site", "creation"),
    ("site_ameliorable", "refonte"), ("ai_takeover_site_amelior", "refonte"), ("site_refonte", "refonte"),
    ("ai_takeover_site_inacces", "refonte"), ("neutre", "neutre"), ("ai_takeover_neutre", "neutre"), (None, None), ("", None),
])
def test_angle_du_brouillon(kind, expected):
    assert complete.draft_angle(kind) == expected
