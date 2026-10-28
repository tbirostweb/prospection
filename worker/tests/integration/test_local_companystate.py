"""État officiel des fiches à prospecter (worker/local/companystate.py, passage `company_state` de l'enrichissement) sur un VRAI MySQL :
liquidation et établissement fermé écartés (raison claire, sortis de la file d'envoi, brouillon gardé), transfert signalé sans rien inventer,
fiche active intacte, passage borné et idempotent, source injoignable = rien de conclu. SIRENE et BODACC simulés (aucun appel réseau réel)."""
from __future__ import annotations

import json

import httpx

from worker import db
from worker.local import companystate, complete, enrich, http

from .test_local_enrich import Lock, _campaign_done, _prov, _set
from .test_local_pipeline import prospect

BISTROT, DUPONT, SALON, TABLE = "11111111100011", "22222222200011", "77777777700011", "88888888800011"
LIQUIDATION = {"familleavis_lib": "Procédures collectives", "typeavis_lib": "Avis initial", "registre": ["222 222 222", "222222222"],
               "dateparution": "2026-03-25", "jugement": json.dumps({"famille": "Jugement d'ouverture",
                                                                   "nature": "Jugement d'ouverture de liquidation judiciaire", "date": "2026-03-17"})}


def _etab(siret, state="A", closed=None, adresse="12 RUE EMILE ZOLA 10000 TROYES"):
    return {"siret": siret, "etat_administratif": state, "date_fermeture": closed, "adresse": adresse}


UNITS = {
    "111111111": {"siren": "111111111", "etat_administratif": "A", "date_fermeture": None,      # établissement fermé, transféré ailleurs
                  "matching_etablissements": [_etab(BISTROT, "F", "2026-09-01")], "siege": _etab("11111111100029", adresse="3 RUE DE LA PAIX 10000 TROYES")},
    "222222222": {"siren": "222222222", "etat_administratif": "A", "matching_etablissements": [_etab(DUPONT)], "siege": _etab(DUPONT)},
    "777777777": {"siren": "777777777", "etat_administratif": "A", "matching_etablissements": [_etab(SALON)], "siege": _etab(SALON)},
    "888888888": {"siren": "888888888", "etat_administratif": "A", "matching_etablissements": [_etab(TABLE)], "siege": _etab(TABLE)},
}


def official(monkeypatch, units=UNITS, bodacc=None, down=None):
    calls: list[str] = []

    def handler(req):
        calls.append(req.url.host)
        if down == req.url.host:
            return httpx.Response(503)
        if req.url.host == "recherche-entreprises.api.gouv.fr":
            assert "etat_administratif" not in req.url.params                  # une entreprise cessée doit être vue
            u = units.get(req.url.params["q"][:9])
            return httpx.Response(200, json={"results": [u] if u else []})
        if req.url.host == "bodacc-datadila.opendatasoft.com":
            where = req.url.params["where"]
            assert "Procédures collectives" in where and "Radiations" in where
            return httpx.Response(200, json={"results": [r for s, recs in (bodacc or {}).items() if f'registre="{s}"' in where for r in recs]})
        raise AssertionError(f"appel inattendu : {req.url}")
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    return calls


def _queued(conn, siret):
    _set(conn, siret, draft_message="Bonjour…", draft_subject="Votre boulangerie", draft_kind="sans_site", auto_state="queued",
         auto_queued_at="2026-10-20 10:00:00")
    conn.commit()


def test_liquidation_et_etablissement_ferme_ecartes_fiche_active_intacte(env, conn, monkeypatch):
    _campaign_done(conn, monkeypatch)
    _queued(conn, DUPONT)
    official(monkeypatch, bodacc={"222222222": [LIQUIDATION]})
    out = companystate.run(conn)
    assert out["done"] == 5 and out["found"] == 2 and out["stop"] == "nothing"

    d = prospect(conn, DUPONT)
    assert d["excluded_reason"] == "liquidation judiciaire (17/03/2026)"
    assert d["auto_state"] is None and d["auto_queued_at"] is None and d["draft_message"] == "Bonjour…"   # sortie de la file, brouillon gardé
    assert "entreprise" in complete.missing(d, True) and complete.channel(d, True) is None
    st = _prov(conn, DUPONT)["company_state"]
    assert st["closed"] is True and st["source"] == "bodacc" and st["bodacc"][-1]["nature"].startswith("Jugement d'ouverture de liquidation")

    b = prospect(conn, BISTROT)
    assert b["excluded_reason"].startswith("établissement fermé (01/09/2026) ; autre établissement actif de l'entreprise (SIRET 11111111100029)")
    st = _prov(conn, BISTROT)["company_state"]
    assert st["active_elsewhere"][0]["siret"] == "11111111100029"                     # transfert signalé, aucune fiche créée ni devinée
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE siret='11111111100029'")["n"] == 0

    s = prospect(conn, SALON)
    assert s["excluded_reason"] is None and _prov(conn, SALON)["company_state"]["closed"] is False    # active : intacte
    # fermées comptées à part dans Statistiques (« Fermées ou en procédure (écartées) »)
    assert db.fetch_one(conn, """SELECT COUNT(*) AS n FROM local_prospects q WHERE q.excluded_reason IS NOT NULL
        AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(q.provenance, '$.company_state.closed')), '') = 'true'""")["n"] == 2


def test_passage_borne_et_idempotent(env, conn, monkeypatch):
    _campaign_done(conn, monkeypatch)
    calls = official(monkeypatch)
    first = companystate.run(conn, limit=1)
    assert first["done"] == 1 and first["stop"] == "limit"
    assert calls.count("recherche-entreprises.api.gouv.fr") == 1 and calls.count("bodacc-datadila.opendatasoft.com") == 1
    assert companystate.run(conn)["done"] == 4
    calls.clear()
    again = companystate.run(conn)
    assert again["done"] == 0 and again["stop"] == "nothing" and calls == []              # tout est frais : aucune requête
    # 30 jours plus tard (fiche non contactée) : revérifiée
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.company_state.checked_at', '2026-01-01T00:00:00Z') WHERE siret=%s", (SALON,))
    assert companystate.run(conn)["done"] == 1


def test_fiche_en_file_revérifiée_tous_les_7_jours(env, conn, monkeypatch):
    _campaign_done(conn, monkeypatch)
    official(monkeypatch)
    companystate.run(conn)
    _queued(conn, SALON)
    week = "DATE_FORMAT(UTC_TIMESTAMP() - INTERVAL 8 DAY, '%%Y-%%m-%%dT%%H:%%i:%%sZ')"
    db.execute(conn, f"UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.company_state.checked_at', {week}) WHERE siret IN (%s,%s)",
               (SALON, TABLE))
    assert [r["siret"] for r in companystate.due(conn, 10)] == [SALON]                    # en file : 7 jours ; les autres : 30 jours


def test_source_injoignable_rien_de_conclu(env, conn, monkeypatch):
    _campaign_done(conn, monkeypatch)
    official(monkeypatch, bodacc={"222222222": [LIQUIDATION]}, down="bodacc-datadila.opendatasoft.com")
    out = companystate.run(conn)
    assert out["stop"] == "error" and out["done"] == 0 and "BODACC" in out["detail"]
    assert "company_state" not in _prov(conn, DUPONT) and prospect(conn, DUPONT)["excluded_reason"] is None
    official(monkeypatch, down="recherche-entreprises.api.gouv.fr")
    out = companystate.run(conn)
    assert out["stop"] == "error" and out["done"] == 0 and "SIRENE" in out["detail"]
    assert all("company_state" not in _prov(conn, s) for s in (BISTROT, DUPONT, SALON, TABLE))


def test_passage_d_enrichissement_ecarte_d_abord(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    official(monkeypatch, bodacc={"222222222": [LIQUIDATION]})
    report = enrich.run(conn, "no_engines", fetch=web, pool_factory=lambda c: search, lock_fn=lambda w: Lock())
    step = report.steps[0]
    assert step["key"] == "company_state" and step["found"] == 2
    assert prospect(conn, DUPONT)["excluded_reason"].startswith("liquidation judiciaire")
    assert "site_guess" not in _prov(conn, DUPONT)                                         # écartée : plus aucun passage ne la travaille
