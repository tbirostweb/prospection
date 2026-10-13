"""Non-régression des migrations : les bugs qui ont bloqué des déploiements."""
from __future__ import annotations

import importlib.util
import pathlib

from worker.migrate import _split_statements

ROOT = pathlib.Path(__file__).resolve().parents[2]
UPDATES = ROOT / "db" / "updates"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_migrations", ROOT / "scripts" / "check_migrations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_toutes_les_migrations_passent_le_garde_fou():
    assert _load_checker().main() == 0


def test_garde_fou_detecte_select_where_sans_from():
    chk = _load_checker()
    bad = "INSERT INTO t (a) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM t WHERE a=1)"
    good = "INSERT INTO t (a) SELECT 1 FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM t WHERE a=1)"
    assert chk.check_select_without_from(bad) is not None
    assert chk.check_select_without_from(good) is None


def test_decoupage_identique_runner_et_garde_fou():
    chk = _load_checker()
    for f in sorted(UPDATES.glob("*.sql")):
        sql = f.read_text(encoding="utf-8")
        assert _split_statements(sql) == chk.split_statements(sql), f.name
