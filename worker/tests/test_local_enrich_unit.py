"""Enrichissement (worker/local/enrich.py), sans base : raison d'arrêt affichée, verrous (attente bornée, verrou propre à l'enrichissement)."""
from __future__ import annotations

from worker.local import enrich, runner


def _report(*steps):
    r = enrich.Report("full")
    r.steps = [{"key": k, "done": 0, "found": 0, "stop": s, "detail": d, "ms": 0} for k, s, d in steps]
    return r


def test_raison_d_arret_la_plus_utile_d_abord():
    assert _report(("recheck", "limit", None), ("email", "nothing", None)).stopped().startswith("plafonds du passage atteints")
    assert _report(("naf", "nothing", None)).stopped() == "rien d'autre à faire"
    r = _report(("places_site", "quota", "Google Places a refusé l'appel (HTTP 429 …)"), ("recheck", "engines", None), ("email", "time", None))
    assert r.stopped().startswith("quota (places_site : Google Places a refusé") and len(r.stopped()) <= 160
    assert _report(("email", "time", None)).stopped() == "budget de temps (email)"
    r.lock_note = "verrou pris : une campagne tournait"
    assert r.stopped() == "verrou pris : une campagne tournait"


def test_verrou_attente_bornee_puis_abandon(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_LOCK", str(tmp_path / "local.lock"))
    held = runner._acquire_lock()
    sleeps = []
    try:
        assert runner._acquire_lock(wait_s=12, poll_s=5, sleep=sleeps.append) is None
        assert 1 <= len(sleeps) <= 3 and all(s == 5 for s in sleeps)                    # quelques essais, jamais une attente sans fin
        assert runner._acquire_lock() is None                                           # sans attente : refus immédiat
        other = runner._acquire_enrich_lock()                                           # l'enrichissement a son propre verrou
        assert other is not None and runner._acquire_enrich_lock() is None
        assert runner.acquire_all_locks() is None                                       # remise à zéro : il faut les deux
        other.close()
    finally:
        held.close()
    both = runner.acquire_all_locks()
    assert both is not None and runner._acquire_lock() is None and runner._acquire_enrich_lock() is None
    both.close()
    assert runner._acquire_lock() is not None


def test_le_verrou_libere_pendant_l_attente_est_pris(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_LOCK", str(tmp_path / "local.lock"))
    held = runner._acquire_lock()
    got = runner._acquire_lock(wait_s=60, poll_s=5, sleep=lambda s: held.close())      # la campagne se termine pendant l'attente
    assert got is not None
    got.close()


def test_ordre_des_sous_passages():
    assert enrich.FREE_STEPS[:3] == ("naf", "manual", "stuck") and "places_site" in enrich.FREE_STEPS
    assert enrich.ENGINE_STEPS == ("recheck", "search_more", "stuck_search", "email")
