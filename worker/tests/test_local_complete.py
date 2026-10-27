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
    assert (why is None) == (case["channel"] == "email")


def test_raison_lisible():
    p = {**DATA["base"], "activity_label": None}
    assert complete.reason(p, True) == "fiche incomplète : métier inconnu (activité non classée)"
