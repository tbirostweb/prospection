"""HTTP simulé : civilité strictement publique, provenance minimale et erreurs réessayables."""
from types import SimpleNamespace

import httpx
import pytest

from worker import scheduler
from worker.local import insee


def unit(sex="M", diffusion="O", category="1000"):
    return {"uniteLegale": {
        "sexeUniteLegale": sex, "statutDiffusionUniteLegale": diffusion,
        "categorieJuridiqueUniteLegale": "5710",  # racine volontairement contradictoire
        "periodesUniteLegale": [
            {"dateFin": "2020-01-01", "categorieJuridiqueUniteLegale": "5710"},
            {"dateFin": None, "categorieJuridiqueUniteLegale": category},
        ],
    }}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    state = SimpleNamespace(provenance={}, updates=[], queries=[], calls=[], slots=0)
    conn = SimpleNamespace(commit=lambda: None)

    def rows(c, sql, params):
        state.queries.append(sql)
        assert "manager_civility IS NULL" in sql and "$.insee" in sql
        return [] if state.provenance else [{"id": 1, "siren": "111111111"}]

    def slot(*args):
        state.slots += 1
        return None

    monkeypatch.setattr(insee.db, "fetch_all", rows)
    monkeypatch.setattr(insee.db, "execute", lambda c, sql, params=(): state.updates.append((sql, params)))
    monkeypatch.setattr(insee.store, "set_provenance", lambda c, pid, k, v: state.provenance.update(v))
    monkeypatch.setattr(insee, "_slot", slot)

    def run(response):
        def handler(req):
            state.calls.append(req)
            assert req.url == insee.API + "111111111"
            assert req.headers["X-INSEE-Api-Key-Integration"] == "test-key"
            return response
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return insee.run(conn, client=client)
    state.run = run
    return state


@pytest.mark.parametrize("sex,civility", [("M", "Monsieur"), ("F", "Madame")])
def test_civilite_et_provenance_minimale(harness, sex, civility):
    h = harness
    assert h.run(httpx.Response(200, json=unit(sex)))["found"] == 1
    assert h.updates[0][1] == (civility, 1)
    assert "TRIM(manager_civility)=''" in h.updates[0][0]
    assert set(h.provenance) == {"source", "status", "checked_at"}
    assert h.provenance["source"] == insee.SOURCE
    assert h.run(httpx.Response(200, json=unit(sex)))["done"] == 0
    assert len(h.calls) == 1  # déjà traité : aucun nouvel appel


@pytest.mark.parametrize("payload,status", [
    (unit(diffusion="N"), "non_diffusible"),
    (unit(diffusion="P"), "non_diffusible"),
    (unit(diffusion=None), "non_diffusible"),
    (unit(category="5710"), "not_individual_entrepreneur"),
    (unit(sex=None), "unavailable"),
    (unit(sex="X"), "unavailable"),
])
def test_pas_de_civilite_hors_conditions(harness, payload, status):
    h = harness
    result = h.run(httpx.Response(200, json=payload))
    assert result["done"] == 1 and result["found"] == 0 and not h.updates
    assert h.provenance["status"] == status
    h.run(httpx.Response(200, json=payload))
    assert len(h.calls) == 1


def test_404_traite_et_429_pause_sans_traitement(harness):
    h = harness
    result = h.run(httpx.Response(429, headers={"Retry-After": "120"}))
    assert result["stop"] == "quota" and result["done"] == 0 and not h.provenance
    assert h.updates[-1][1] == (120, 120)
    assert h.run(httpx.Response(404))["done"] == 1
    assert h.provenance["status"] == "not_found"
    h.run(httpx.Response(404))
    assert len(h.calls) == 2


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(401), httpx.Response(200, text="invalid")])
def test_erreurs_reessayables(harness, response):
    assert harness.run(response)["stop"] == "error"
    assert not harness.provenance and not harness.updates


def test_sans_cle_aucun_travail_et_transmission_planificateur(monkeypatch):
    monkeypatch.delenv("INSEE_API_KEY", raising=False)
    def forbidden(*a, **kw):
        pytest.fail("aucun accès sans clé")
    monkeypatch.setattr(insee.db, "fetch_all", forbidden)
    monkeypatch.setattr(insee.httpx, "Client", forbidden)
    assert insee.run(None)["stop"] == "off"
    assert scheduler.job_env({"INSEE_API_KEY": "test-key"}) == {"INSEE_API_KEY": "test-key"}


def test_periode_actuelle_uniquement():
    p = unit()["uniteLegale"]
    p["periodesUniteLegale"][0]["categorieJuridiqueUniteLegale"] = "1000"
    p["periodesUniteLegale"][1]["categorieJuridiqueUniteLegale"] = "5710"
    p["categorieJuridiqueUniteLegale"] = "1000"
    assert insee._civil_status(p)[0] is None
    p.pop("periodesUniteLegale")
    assert insee._civil_status(p)[0] is None


def test_cadence_partagee_et_pause_connue(monkeypatch):
    clock = [0.0]
    state = {"next": 0.0, "pause": False}
    def execute(c, sql, params=()):
        if sql.startswith("UPDATE"):
            state["next"] = clock[0] + params[0] / 1_000_000
    monkeypatch.setattr(insee.db, "execute", execute)
    monkeypatch.setattr(insee.db, "fetch_one", lambda *a: {"wait_s": max(0, state["next"] - clock[0]), "paused": state["pause"]})
    conn = SimpleNamespace(commit=lambda: None)
    def sleep(s):
        clock[0] += s
    starts = []
    for _ in range(31):
        assert insee._slot(conn, None, lambda: clock[0], sleep) is None
        starts.append(clock[0])
    assert starts[-1] - starts[0] > 60
    assert insee._slot(conn, clock[0] + 1, lambda: clock[0], sleep) == "time"
    state["pause"] = True
    previous = clock[0]
    assert insee._slot(conn, None, lambda: clock[0], sleep) == "quota"
    assert clock[0] == previous


def test_plafond_defaut_et_desactivation(monkeypatch):
    monkeypatch.delenv("LOCAL_INSEE_PER_RUN", raising=False)
    assert insee._limit() == 50
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    monkeypatch.setenv("LOCAL_INSEE_PER_RUN", "0")
    assert insee.run(None)["stop"] == "off"
