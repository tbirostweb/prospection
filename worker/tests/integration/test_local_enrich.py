"""Enrichissement des fiches existantes (worker/local/enrich.py) sur un VRAI MySQL : ordre et verrous (moteurs seulement sous le verrou des
campagnes, partie sans moteur pendant une campagne), budgets de temps, passages nouveaux (devinettes, sites injoignables, recherche complétée,
sites relus, fiches bloquées) idempotents et bornés, état du dernier passage, réserves Google Places du jour et du mois. Aucun appel réseau réel."""
from __future__ import annotations

import json
import time

import pytest

from worker import db
from worker.local import complete, emailsearch, enrich, places, runner, sitefinder, siterecheck
from worker.local.engines import Hit

from .test_local_pipeline import add_campaign, install_api, prospect, web_and_search
from .test_local_siterecheck import DUPONT, LEGAL, SITE, _setup

BISTROT = "11111111100011"


class Lock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _campaign_done(conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    db.execute(conn, "UPDATE local_campaigns SET status='done'")
    search.queries.clear()
    return web, search


def _set(conn, siret, **cols):
    db.execute(conn, f"UPDATE local_prospects SET {', '.join(f'{k}=%s' for k in cols)} WHERE siret=%s", (*cols.values(), siret))


def _prov(conn, siret) -> dict:
    return json.loads(prospect(conn, siret)["provenance"] or "{}")


@pytest.fixture(autouse=True)
def _places_off(monkeypatch):
    monkeypatch.setattr(places, "API_KEY", "")                                     # aucune requête Google : passages Places « off »
    monkeypatch.setattr(places, "_blocked", False)
    monkeypatch.setattr(places, "PURPOSE", "campaign")


# ── Ordre, verrous, budgets ─────────────────────────────────────────────────────────────────────────
def test_verrou_pris_partie_sans_moteur_puis_attente_bornee(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    waits = []
    report = enrich.run(conn, "full", fetch=web, pool_factory=lambda c: search, lock_fn=lambda w: waits.append(w))
    keys = [s["key"] for s in report.steps]
    assert keys == list(enrich.FREE_STEPS) + list(enrich.ENGINE_STEPS)                  # sans moteur d'abord, puis les moteurs (refusés)
    assert waits[0] == 0 and 0 < waits[1] <= enrich.LOCK_WAIT_S                         # essai immédiat, puis attente bornée
    assert all(s["stop"] == "lock" for s in report.steps if s["key"] in enrich.ENGINE_STEPS) and search.queries == []
    assert "verrou pris" in report.stopped()
    report.save(conn)
    row = db.fetch_one(conn, "SELECT * FROM local_enrich_runs ORDER BY id DESC LIMIT 1")
    assert row["mode"] == "full" and "verrou pris" in row["stopped"] and len(json.loads(row["steps"])) == len(keys)


def test_verrou_libre_moteurs_d_abord_puis_relache(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    lock = Lock()
    calls = []
    report = enrich.run(conn, "full", fetch=web, pool_factory=lambda c: search, lock_fn=lambda w: calls.append(w) or lock)
    keys = [s["key"] for s in report.steps]
    assert keys == list(enrich.ENGINE_STEPS) + list(enrich.FREE_STEPS) and lock.closed and calls == [0]
    assert report.lock_note is None


def test_sans_moteur_jamais_le_verrou_des_campagnes(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    report = enrich.run(conn, "no_engines", fetch=web, pool_factory=lambda c: pytest.fail("aucun moteur"),
                        lock_fn=lambda w: pytest.fail("aucun verrou des campagnes"))
    assert [s["key"] for s in report.steps] == list(enrich.FREE_STEPS)


def test_moteurs_indisponibles_les_sous_passages_suivants_n_insistent_pas(env, conn, monkeypatch):
    from worker.local.errors import SearchUnavailable
    web, _search = _campaign_done(conn, monkeypatch)
    _set(conn, DUPONT, email=None)

    def down(_q):
        raise SearchUnavailable("tous les moteurs sont en cooldown")
    report = enrich.run(conn, "full", fetch=web, pool_factory=lambda c: down, lock_fn=lambda w: Lock())
    eng = [s for s in report.steps if s["key"] in enrich.ENGINE_STEPS]
    assert eng[0]["stop"] == "engines" and all(s["stop"] == "engines" for s in eng) and "moteurs indisponibles" in report.stopped()


def test_budget_de_temps_coupe_le_sous_passage(env, conn, monkeypatch):
    web, _search = _campaign_done(conn, monkeypatch)
    out = enrich.site_guess(conn, web, deadline=time.monotonic() - 1)
    assert out["stop"] == "time" and out["done"] == 0
    monkeypatch.setattr(enrich, "STEP_BUDGET_S", 0)                                     # chaque sous-passage n'a plus de temps
    report = enrich.run(conn, "no_engines", fetch=web)
    assert next(s for s in report.steps if s["key"] == "site_guess")["stop"] == "time" and "budget de temps" in report.stopped()


def test_etat_garde_les_derniers_passages_et_passage_saute(env, conn, monkeypatch):
    monkeypatch.setattr(enrich, "KEEP_RUNS", 3)
    for _ in range(5):
        enrich.Report("no_engines").save(conn)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_enrich_runs")["n"] == 3
    held = runner._acquire_enrich_lock()                                               # un passage précédent tourne encore
    try:
        assert enrich.main([]) == 0
    finally:
        held.close()
    row = db.fetch_one(conn, "SELECT * FROM local_enrich_runs ORDER BY id DESC LIMIT 1")
    assert row["mode"] == "skipped" and "encore en cours" in row["stopped"]


def test_main_passe_complet_enregistre_son_etat(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    monkeypatch.setattr(enrich.engines, "SearchPool", lambda c: search)
    monkeypatch.setattr("worker.local.fetch.Fetcher", lambda *a, **k: web)
    assert emailsearch.main([]) == 0 and places.PURPOSE == "enrich"
    row = db.fetch_one(conn, "SELECT * FROM local_enrich_runs ORDER BY id DESC LIMIT 1")
    steps = {s["key"]: s for s in json.loads(row["steps"])}
    assert row["mode"] == "full" and set(steps) == set(enrich.FREE_STEPS) | set(enrich.ENGINE_STEPS) and row["stopped"]


# ── Passages ────────────────────────────────────────────────────────────────────────────────────────
def test_devinettes_sans_moteur_trouvent_le_site_une_seule_fois(env, conn, monkeypatch):
    web, search = _setup(conn, monkeypatch)
    out = enrich.site_guess(conn, web)
    p = prospect(conn, DUPONT)
    assert out["found"] == 1 and p["website_status"] == "CONFIRMED" and p["email"] == "contact@boulangeriedupont.fr" and search.queries == []
    assert p["draft_message"] is None                                                   # brouillon « sans site » effacé
    assert _prov(conn, DUPONT)["site_guess"]["found"] is True
    runs = [enrich.site_guess(conn, web)["done"] for _ in range(3)]
    assert runs[-1] == 0                                                                # chaque fiche une seule fois : le passage se tarit


def test_devinettes_levent_un_doute_d_avant_envoi_quand_rien_ne_ressort(env, conn, monkeypatch):
    web, _search = _campaign_done(conn, monkeypatch)
    prov = {"site_recheck": {"status": "NOT_FOUND"}, "presend_site": {"at": "2026-10-26T10:00:00Z", "doubt": True, "result": "doubt"},
            "site_guess": {"checked_at": "2026-10-01T10:00:00Z", "status": "NOT_FOUND"}}
    db.execute(conn, "UPDATE local_prospects SET website_absence_confidence=0.7, provenance=JSON_MERGE_PATCH(COALESCE(provenance, '{}'), %s) WHERE siret=%s",
               (json.dumps(prov), DUPONT))
    assert complete.site_reason(prospect(conn, DUPONT)) == "presend"
    enrich.site_guess(conn, web)
    p = prospect(conn, DUPONT)
    assert complete.site_reason(p) is None and _prov(conn, DUPONT)["presend_site"]["cleared_by"] == "site_guess"
    enrich.site_guess(conn, web)
    assert prospect(conn, DUPONT)["provenance"] == p["provenance"]                       # doute levé : plus repris (pas de boucle)


def test_site_injoignable_deux_nouveaux_essais_puis_en_panne(env, conn, monkeypatch):
    web, _search = _campaign_done(conn, monkeypatch)
    _set(conn, DUPONT, website_status="UNREACHABLE", website_url="https://dupont-en-panne.fr/", website_checked_at="2026-01-01 00:00:00")
    assert complete.site_reason(prospect(conn, DUPONT)) == "unreachable"
    assert enrich.unreachable_retry(conn, web)["done"] == 1 and _prov(conn, DUPONT)["unreachable_retry"]["tries"] == 1
    assert enrich.unreachable_retry(conn, web)["done"] == 0                             # pas avant 3 jours
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.unreachable_retry.checked_at', '2026-01-01T00:00:00Z') WHERE siret=%s", (DUPONT,))
    enrich.unreachable_retry(conn, web)
    p = prospect(conn, DUPONT)
    assert _prov(conn, DUPONT)["unreachable_retry"] == {**_prov(conn, DUPONT)["unreachable_retry"], "tries": 2, "status": "UNREACHABLE"}
    assert complete.site_reason(p) is None and complete.unreachable_settled(p)          # « site en panne » : tranché
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.unreachable_retry.checked_at', '2026-01-01T00:00:00Z') WHERE siret=%s", (DUPONT,))
    assert enrich.unreachable_retry(conn, web)["done"] == 0                             # jamais plus de 2 essais


def test_site_injoignable_revenu_enregistre_ou_redevenu_sans_site(env, conn, monkeypatch):
    web, _search = _campaign_done(conn, monkeypatch)
    _set(conn, DUPONT, website_status="UNREACHABLE", website_url=f"https://{SITE}/", website_checked_at="2026-01-01 00:00:00")
    web.add_site(SITE, "<html><head><title>Boulangerie Dupont Troyes</title></head><body><h1>Boulangerie Dupont</h1><a href='/mentions-legales'>Mentions</a></body></html>",
                 legal=LEGAL)
    assert enrich.unreachable_retry(conn, web)["found"] == 1
    assert prospect(conn, DUPONT)["website_status"] in ("CONFIRMED", "PROBABLE")
    # un autre prospect dont le domaine répond mais n'est plus le sien : il redevient « sans site », à chercher
    _set(conn, "77777777700011", website_status="UNREACHABLE", website_url="https://la-table-ronde.fr/", website_checked_at="2026-01-01 00:00:00")
    enrich.unreachable_retry(conn, web)
    p = prospect(conn, "77777777700011")
    assert p["website_status"] in ("NOT_FOUND", "UNCERTAIN") and complete.site_reason(p) in ("search", "uncertain", "uncertain_user")


def test_recherche_trop_maigre_completee_sans_refaire_les_strategies(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    ev = json.loads(prospect(conn, DUPONT)["website_evidence"])
    first = ev["strategies"][:2]
    _set(conn, DUPONT, website_absence_confidence=0.3, website_search_tried=2,
         website_evidence=json.dumps({**ev, "strategies": first, "degraded": False}))
    assert complete.site_reason(prospect(conn, DUPONT)) == "search"
    out = enrich.search_more(conn, search, web)
    p = prospect(conn, DUPONT)
    assert out["done"] == 1 and float(p["website_absence_confidence"]) >= 0.6
    assert '"BOULANGERIE DUPONT" "TROYES"' not in search.queries                       # « nom + ville » déjà essayé : pas refait
    assert complete.site_reason(p) is None                                              # toutes les vérifications faites, rien trouvé : tranché
    assert _prov(conn, DUPONT)["site_search_more"]["tries"] == 1 and enrich.search_more(conn, search, web)["done"] == 0   # pas avant 7 jours


def test_site_verifie_sans_email_relu_une_fois(env, conn, monkeypatch):
    web, _search = _campaign_done(conn, monkeypatch)
    _set(conn, BISTROT, email=None, email_kind=None)
    out = enrich.site_emails(conn, web)
    assert out["found"] == 1 and prospect(conn, BISTROT)["email"] == "contact@bistrot-martin.fr"
    _set(conn, BISTROT, email=None, email_kind=None)
    assert enrich.site_emails(conn, web)["done"] == 0                                   # une seule fois par fiche


def test_fiches_bloquees_hors_campagne_terminees_campagne_en_cours_intacte(env, conn, monkeypatch):
    web, _search = _campaign_done(conn, monkeypatch)
    _set(conn, DUPONT, pipeline_stage="SCORING")
    _set(conn, BISTROT, pipeline_stage="ERROR", next_retry_at=None, attempts=3, error_category="SITE_TIMEOUT")
    out = enrich.finish_stuck(conn, web)
    assert out["done"] == 2 and prospect(conn, DUPONT)["pipeline_stage"] == "DONE" and prospect(conn, BISTROT)["pipeline_stage"] == "DONE"
    _set(conn, DUPONT, pipeline_stage="SCORING")
    assert enrich.finish_stuck(conn, web)["done"] == 0                                  # une seule reprise par fiche
    db.execute(conn, "UPDATE local_campaigns SET status='running'")
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_REMOVE(provenance, '$.stuck_retry') WHERE siret=%s", (DUPONT,))
    assert enrich.finish_stuck(conn, web)["done"] == 0                                  # campagne en cours : c'est elle qui finit ses fiches


def test_revérification_sans_lien_de_campagne(env, conn, monkeypatch):
    web, search = _setup(conn, monkeypatch)
    db.execute(conn, "DELETE FROM local_prospect_campaigns")                            # fiche importée : aucune campagne
    assert prospect(conn, DUPONT)["id"] in [r["id"] for r in siterecheck.candidates(conn, 50)]
    stats = siterecheck.run(conn, search, fetch=web, limit=10)
    assert stats["found"] == 1 and prospect(conn, DUPONT)["website_status"] == "CONFIRMED"


# ── Google Places : quota du jour et réserves ──────────────────────────────────────────────────────
def test_quota_du_jour_et_reserves_des_fiches_existantes(env, conn, monkeypatch):
    db.execute(conn, "DELETE FROM local_places_usage")
    monkeypatch.setattr(places, "DAILY_BUDGET", 10)
    monkeypatch.setattr(places, "MONTHLY_BUDGET", 1000)
    monkeypatch.setattr(places, "ENRICH_DAILY_RESERVE", 60)                             # plafonnée au cinquième : 2 requêtes du jour gardées
    got = [places.reserve(conn) for _ in range(9)]
    assert got == [True] * 8 + [False] and "réserve du jour" in places.stop_reason and places.day_used(conn) == 8
    monkeypatch.setattr(places, "PURPOSE", "enrich")
    assert places.reserve(conn) and places.reserve(conn) and not places.reserve(conn)
    assert "quota Google du jour" in places.stop_reason and places.day_used(conn) == 10 and places.month_used(conn) == 10
    places._refund(conn)
    assert places.day_used(conn) == 9 and places.month_used(conn) == 9
    row = db.fetch_one(conn, "SELECT budget FROM local_places_daily")
    assert row["budget"] == 10


def test_budget_du_mois_refuse_sans_consommer_le_jour(env, conn, monkeypatch):
    db.execute(conn, "DELETE FROM local_places_usage")
    monkeypatch.setattr(places, "DAILY_BUDGET", 300)
    monkeypatch.setattr(places, "MONTHLY_BUDGET", 1)
    assert places.reserve(conn) and not places.reserve(conn)
    assert "budget mensuel" in places.stop_reason and places.day_used(conn) == 1


# ── Trancher sans attendre : chaque « site à trancher » a un passage prévu ─────────────────────────────────────────────────────────────
def test_revérification_en_erreur_reprise_puis_absence_sure(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    prov = {"site_recheck": {"checked_at": "2026-10-01T10:00:00Z", "error": "ReadTimeout"}}
    db.execute(conn, "UPDATE local_prospects SET website_absence_confidence=0.7, provenance=JSON_MERGE_PATCH(COALESCE(provenance, '{}'), %s) WHERE siret=%s",
               (json.dumps(prov), DUPONT))
    assert complete.site_reason(prospect(conn, DUPONT)) == "recheck"                    # autrefois : plus jamais reprise (aucun passage prévu)
    enrich.run(conn, "full", fetch=web, pool_factory=lambda c: search, lock_fn=lambda w: Lock())
    p = prospect(conn, DUPONT)
    assert _prov(conn, DUPONT)["site_recheck"]["status"] == "NOT_FOUND" and _prov(conn, DUPONT)["site_guess"]["version"] == sitefinder.GUESS_VERSION
    assert complete.site_reason(p) is None                                              # tous les passages ont tourné : absence de site sûre


def test_tous_les_passages_tournes_absence_sure_en_un_passage(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    ev = json.loads(prospect(conn, DUPONT)["website_evidence"])
    _set(conn, DUPONT, website_absence_confidence=0.3, website_evidence=json.dumps({**ev, "strategies": ev["strategies"][:1], "degraded": True}))
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_REMOVE(COALESCE(provenance, JSON_OBJECT()), '$.site_recheck') WHERE siret=%s", (DUPONT,))
    assert complete.site_reason(prospect(conn, DUPONT)) == "search"
    enrich.run(conn, "full", fetch=web, pool_factory=lambda c: search, lock_fn=lambda w: Lock())
    p = prospect(conn, DUPONT)
    assert float(p["website_absence_confidence"]) >= 0.6 and complete.site_reason(p) is None   # recherche refaite ET revérification posée
    assert p["website_status"] == "NOT_FOUND"


def test_recherche_jamais_faite_sur_fiche_terminee_reprise(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    _set(conn, DUPONT, website_status=None, website_absence_confidence=None)
    assert complete.site_reason(prospect(conn, DUPONT)) == "search"
    assert enrich.search_more(conn, search, web)["done"] == 1
    p = prospect(conn, DUPONT)
    assert p["website_status"] == "NOT_FOUND" and float(p["website_absence_confidence"]) >= 0.6 and complete.site_reason(p) is None


def test_recherche_degradee_retentee_le_lendemain_puis_toutes_les_deux_semaines(env, conn, monkeypatch):
    web, search = _campaign_done(conn, monkeypatch)
    _set(conn, DUPONT, website_absence_confidence=0.3)
    for tries, days, due in ((1, 2, True), (3, 2, False), (3, 15, True)):
        flag = {"checked_at": dt_iso(days), "tries": tries}
        db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(COALESCE(provenance, JSON_OBJECT()), '$.site_search_more', CAST(%s AS JSON)) WHERE siret=%s",
                   (json.dumps(flag), DUPONT))
        got = enrich.search_more(conn, _NoHit(), web)
        assert (got["done"] >= 1) is due, (tries, days)
        _set(conn, DUPONT, website_absence_confidence=0.3)


class _NoHit:
    """Moteur dégradé : aucun résultat (la confiance d'absence ne monte pas assez)."""
    last = type("L", (), {"degraded": True})()

    def __call__(self, query):
        return []


def dt_iso(days_ago: int) -> str:
    import datetime as _dt
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
