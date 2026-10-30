"""Vrai SQL : migration rejouable, champs vides seuls, mémoire et pause partagée."""
import json
from pathlib import Path

import httpx

from worker import db, migrate
from worker.local import insee


def test_migration_et_enrichissement_insee(env_sans_telegram, conn, monkeypatch):
    # Rejouer même avec une colonne déjà présente ne doit pas échouer.
    sql = Path(__file__).resolve().parents[3] / "db/updates/036_insee_civilite.sql"
    for _ in range(2):
        for stmt in migrate._split_statements(sql.read_text()):
            db.execute(conn, stmt)
    db.execute(conn, "DELETE FROM local_insee_state")
    def add(siren, civility=None):
        return db.execute(conn, """INSERT INTO local_prospects
            (siren, siret, fingerprint, company_name, manager_civility, pipeline_stage, status)
            VALUES (%s,%s,%s,'Fixture INSEE',%s,'DONE','ENRICHED')""", (siren, siren + "00011", siren, civility))
    pid = add("111111111")
    kept = add("222222222", "Madame")
    conn.commit()
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"uniteLegale": {
            "statutDiffusionUniteLegale": "O", "sexeUniteLegale": "M",
            "periodesUniteLegale": [{"dateFin": None, "categorieJuridiqueUniteLegale": "1000"}],
        }})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert insee.run(conn, client=client)["found"] == 1
        assert insee.run(conn, client=client)["done"] == 0
    row = db.fetch_one(conn, "SELECT manager_civility, provenance FROM local_prospects WHERE id=%s", (pid,))
    assert row["manager_civility"] == "Monsieur"
    assert set(json.loads(row["provenance"])["insee"]) == {"source", "status", "checked_at"}
    assert db.fetch_one(conn, "SELECT manager_civility FROM local_prospects WHERE id=%s", (kept,))["manager_civility"] == "Madame"
    assert len(calls) == 1
    retry = add("333333333")
    db.execute(conn, "UPDATE local_insee_state SET next_request_at=UTC_TIMESTAMP(6)")
    conn.commit()
    calls.clear()
    def quota(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "120"})
    with httpx.Client(transport=httpx.MockTransport(quota)) as client:
        assert insee.run(conn, client=client)["stop"] == "quota"
        assert insee.run(conn, client=client)["stop"] == "quota"
    assert len(calls) == 1
    assert db.fetch_one(conn, "SELECT provenance FROM local_prospects WHERE id=%s", (retry,))["provenance"] is None
    db.execute(conn, "DELETE FROM local_insee_state")
    conn.commit()
