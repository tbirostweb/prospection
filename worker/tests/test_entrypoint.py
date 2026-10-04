"""entrypoint.sh : une migration en échec ARRÊTE le conteneur (aucune tâche sur un schéma incomplet)."""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

ENTRY = pathlib.Path(__file__).resolve().parents[1] / "entrypoint.sh"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="test du chemin non root")


def _run(tmp_path, migrate_rc=0, check_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    fake = bindir / "python"
    fake.write_text(f"""#!/bin/sh
echo "$*" >> "{calls}"
case "$*" in
  "-m worker.migrate") exit {migrate_rc} ;;
  "-m worker.migrate check") exit {check_rc} ;;
  *) exit 0 ;;
esac
""")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "APP_DIR": str(tmp_path), "LOG_DIR": str(tmp_path / "logs")}
    p = subprocess.run(["sh", str(ENTRY)], env=env, capture_output=True, text=True, timeout=30)
    return p, (calls.read_text().splitlines() if calls.exists() else [])


def test_migration_en_echec_arrete_le_worker(tmp_path):
    p, calls = _run(tmp_path, migrate_rc=1)
    assert p.returncode != 0
    assert "ÉCHEC des migrations" in p.stderr
    assert "-m worker.scheduler" not in calls and "-m worker.local.rescore" not in calls


def test_schema_incomplet_arrete_le_worker(tmp_path):
    p, calls = _run(tmp_path, check_rc=1)
    assert p.returncode != 0 and "-m worker.scheduler" not in calls


def test_demarrage_nominal_lance_le_planificateur(tmp_path):
    p, calls = _run(tmp_path)
    assert p.returncode == 0, p.stderr
    assert calls == ["-m worker.migrate", "-m worker.migrate check", "-m worker.local.rescore", "-m worker.scheduler"]
    assert oct((tmp_path / "logs").stat().st_mode & 0o777) == "0o750"


def test_plus_de_crontab_avec_secrets():
    text = ENTRY.read_text()
    assert "crontab" not in text and "printenv" not in text
