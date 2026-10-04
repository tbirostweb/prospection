"""Planificateur non root (remplace cron) : mêmes horaires, aucun secret sur disque, environnement minimal des tâches."""
from __future__ import annotations

import datetime as dt

import pytest

from worker import scheduler

# Crontab d'origine (worker/entrypoint.sh avant passage au planificateur) : les horaires ne doivent pas changer.
OLD_CRONTAB = {
    ("*/5 * * * *", "worker.telegram_poll"), ("*/15 6-22 * * *", "worker.local.run"), ("10 7 * * *", "worker.local.watch"),
    ("* * * * *", "worker.reset"), ("5 * * * *", "worker.local.monitor"), ("15 7 * * *", "worker.digest"),
    ("0 8 * * 1", "worker.digest"), ("30 4 * * *", "worker.local.retention"), ("0 4 * * *", None),
}


def test_memes_taches_et_horaires_que_l_ancienne_crontab():
    assert {(e, a[1] if a else None) for e, a, _ in scheduler.JOBS} == OLD_CRONTAB


@pytest.mark.parametrize("expr,when,expected", [
    ("*/5 * * * *", dt.datetime(2026, 10, 5, 3, 10), True),
    ("*/5 * * * *", dt.datetime(2026, 10, 5, 3, 11), False),
    ("*/15 6-22 * * *", dt.datetime(2026, 10, 5, 22, 45), True),
    ("*/15 6-22 * * *", dt.datetime(2026, 10, 5, 23, 0), False),
    ("*/15 6-22 * * *", dt.datetime(2026, 10, 5, 5, 45), False),
    ("0 8 * * 1", dt.datetime(2026, 10, 5, 8, 0), True),      # lundi
    ("0 8 * * 1", dt.datetime(2026, 10, 4, 8, 0), False),     # dimanche
    ("0 8 * * 0", dt.datetime(2026, 10, 4, 8, 0), True),
    ("0 8 * * 7", dt.datetime(2026, 10, 4, 8, 0), True),      # 7 = dimanche
    ("0 0 1 * 1", dt.datetime(2026, 10, 5, 0, 0), True),      # jour du mois OU jour de semaine
    ("1,2 3 * * *", dt.datetime(2026, 1, 1, 3, 2), True),
])
def test_correspondance_cron(expr, when, expected):
    assert scheduler.matches(expr, when) is expected


@pytest.mark.parametrize("bad", ["* * * *", "61 * * * *", "*/0 * * * *", "5-1 * * * *", "a * * * *"])
def test_expression_invalide_refusee(bad):
    with pytest.raises(ValueError):
        scheduler.parse(bad)


def test_environnement_des_taches_sans_secrets_inutiles():
    env = scheduler.job_env({"DATABASE_URL": "mysql://u:p@h/d", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_IDS": "1",
                             "LOCAL_BRAVE_API_KEY": "k", "PATH": "/usr/bin", "APP_PASSWORD": "secret", "MYSQL_ROOT_PASSWORD": "root",
                             "SEARXNG_SECRET": "s", "MYSQL_PASSWORD": "x"})
    assert set(env) == {"DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "LOCAL_BRAVE_API_KEY", "PATH"}


def test_lancement_des_taches_dues_et_rotation(tmp_path, monkeypatch):
    calls = []

    class FakeProc:
        def __init__(self, args, **kw):
            calls.append((args, kw))
        def poll(self):
            return 0

    monkeypatch.setattr(scheduler, "APP_DIR", tmp_path)
    s = scheduler.Scheduler(log_dir=tmp_path, popen=FakeProc)
    started = s.run_due(dt.datetime(2026, 10, 5, 4, 0))                   # 04:00 : rotation + reset + telegram (minute 0)
    mods = sorted(a[1] for a in started if a)
    assert mods == ["worker.reset", "worker.telegram_poll"] and None in started
    assert all("APP_PASSWORD" not in kw["env"] for _, kw in calls)
    assert (tmp_path / "local.log").exists() and (tmp_path / "telegram.log").exists()
    assert s.children == []                                               # processus terminés récoltés (pas de zombies)


def test_rotation_garde_les_dernieres_lignes(tmp_path):
    f = tmp_path / "x.log"
    f.write_text("".join(f"l{i}\n" for i in range(20)))
    scheduler.trim_logs(tmp_path, keep=5)
    assert f.read_text().splitlines() == [f"l{i}" for i in range(15, 20)]
    assert oct(f.stat().st_mode & 0o777) == "0o640"


@pytest.mark.parametrize("setting", [None, "false", "1", "TRUE", "true"])
def test_retention_requires_explicit_authorization(monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("RETENTION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RETENTION_ENABLED", setting)
    jobs = scheduler.Scheduler().jobs
    retention = [j for j in jobs if j[1] == ["-m", "worker.local.retention"]]
    assert bool(retention) is (setting == "true")
    assert len(jobs) == len(scheduler.JOBS) - (setting != "true")
