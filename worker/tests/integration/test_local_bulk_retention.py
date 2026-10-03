"""Export SIRENE en masse pour une campagne, et conservation raisonnable des données (vrai MySQL)."""
from __future__ import annotations

import httpx

from worker import db
from worker.local import bulk, http, retention

from .test_local_pipeline import add_campaign

HEADER = ("siret,siren,etatAdministratifEtablissement,statutDiffusionEtablissement,activitePrincipaleEtablissement,codePostalEtablissement,"
          "libelleCommuneEtablissement,libelleVoieEtablissement,typeVoieEtablissement,numeroVoieEtablissement,denominationUsuelleEtablissement,"
          "enseigne1Etablissement,dateCreationEtablissement,trancheEffectifsEtablissement,etablissementSiege,codeCommuneEtablissement\n")


def test_import_de_l_export_sirene_pour_une_campagne(env, conn, monkeypatch, tmp_path):
    f = tmp_path / "stock.csv"
    f.write_text(HEADER
                 + "11111111100011,111111111,A,O,43.22A,10000,TROYES,ZOLA,RUE,1,PLOMBERIE MARTIN,,2025-01-01,01,true,10387\n"
                 + "11111111100011,111111111,A,O,43.22A,10000,TROYES,ZOLA,RUE,1,PLOMBERIE MARTIN,,2025-01-01,01,true,10387\n"   # doublon
                 + "22222222200011,222222222,F,O,43.22A,10000,TROYES,ZOLA,RUE,2,FERMEE,,2010-01-01,01,true,10387\n"             # fermée
                 + "33333333300011,333333333,A,N,43.22A,10000,TROYES,ZOLA,RUE,3,NON DIFFUSABLE,,2010-01-01,01,true,10387\n"     # non diffusable
                 + "44444444400011,444444444,A,O,56.10A,10000,TROYES,ZOLA,RUE,4,RESTO,,2010-01-01,01,true,10387\n"              # hors activité
                 + "55555555500011,555555555,A,O,43.22A,10150,LOIN,ZOLA,RUE,5,PLOMBERIE LOIN,,2010-01-01,01,true,10999\n"       # hors rayon
                 + "66666666600011,666666666,A,O,43.22A,10000,TROYES,ZOLA,RUE,6,,CENTURY 21,2010-01-01,01,true,10387\n",         # enseigne bannie
                 encoding="utf-8")

    def geo(req):
        assert req.url.host == "geo.api.gouv.fr"
        return httpx.Response(200, json={"centre": {"coordinates": [4.0761, 48.2924] if "10387" in req.url.path else [5.5, 48.9]}})
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(geo))
    camp = add_campaign(conn)
    db.execute(conn, "UPDATE local_campaigns SET activities='[\"plombiers\"]', status='done' WHERE id=%s", (camp["id"],))
    conn.commit()
    camp = db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],))
    stats = bulk.import_for_campaign(conn, str(f), camp)
    rows = db.fetch_all(conn, "SELECT siret, geo_confidence, distance_km, source FROM local_prospects p JOIN local_prospect_sources s ON s.prospect_id=p.id")
    assert [r["siret"] for r in rows] == ["11111111100011"] and rows[0]["source"] == "sirene_bulk"
    assert float(rows[0]["geo_confidence"]) <= bulk.COMMUNE_GEO_CONF and float(rows[0]["distance_km"]) == 0.0      # centre de commune : jamais « précis »
    assert stats["banned"] == 1 and stats["out_of_radius"] == 1 and stats["imported"] == 2 and stats["new"] == 1       # doublon fusionné
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "queued"


def test_conservation_raisonnable(env, conn):
    def add(siret, months_ago, **over):
        row = {"siret": siret, "fingerprint": siret, "company_name": siret, **over}
        pid = db.execute(conn, f"INSERT INTO local_prospects ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
        db.execute(conn, "UPDATE local_prospects SET discovered_at = UTC_TIMESTAMP() - INTERVAL %s MONTH WHERE id=%s", (months_ago, pid))
    add("o_untouched", 20)
    add("r_untouched", 3)
    add("o_starred", 20, status="TO_CONTACT")
    add("o_note", 20, notes="à rappeler en janvier")
    add("o_contacted", 20, status="LOST", contacted_at="2025-01-01", response_status="NOT_INTERESTED")
    add("excl_7m", 7, excluded_reason="agence web")
    add("chain_2m", 2, is_chain=1)
    add("dnc", 30, do_not_contact=1, status="DO_NOT_CONTACT")
    db.execute(conn, "INSERT INTO local_do_not_contact (siret, reason) VALUES ('dnc','a demandé')")
    db.execute(conn, "INSERT INTO local_http_cache (cache_key, body, fetched_at) VALUES ('old','{}', UTC_TIMESTAMP() - INTERVAL 90 DAY), ('new','{}', UTC_TIMESTAMP())")
    conn.commit()
    out = retention.purge(conn)
    left = {r["siret"] for r in db.fetch_all(conn, "SELECT siret FROM local_prospects")}
    assert left == {"r_untouched", "o_starred", "o_note", "o_contacted", "chain_2m", "dnc"}
    assert out["prospects_untouched"] == 1 and out["prospects_excluded"] == 1 and out["http_cache"] == 1
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_do_not_contact")["n"] == 1
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_outcomes WHERE okey='o_contacted'")["n"] == 1           # résultat archivé avant tout
