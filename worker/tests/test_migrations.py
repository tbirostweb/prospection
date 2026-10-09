"""Non-régression migrations & connecteurs : les bugs qui ont bloqué des déploiements."""
from __future__ import annotations

import importlib.util
import pathlib

from worker.connectors.base import Connector
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


def test_005_leve_unique_connector_avant_insert():
    stmts = _split_statements((UPDATES / "005_sources_v2.sql").read_text(encoding="utf-8"))
    joined = [s.upper() for s in stmts]
    drop = next(i for i, s in enumerate(joined) if "DROP INDEX CONNECTOR" in s)
    insert = next(i for i, s in enumerate(joined) if s.startswith("INSERT INTO SOURCES"))
    assert drop < insert


def test_schema_initial_connector_non_unique():
    sql = (ROOT / "db" / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    line = next(ln for ln in sql.splitlines() if ln.strip().startswith("connector"))
    assert "UNIQUE" not in line.upper()


def test_config_json_texte_parse_en_dict():
    # PyMySQL renvoie les colonnes JSON en texte : bug "'str' object has no attribute 'get'"
    assert Connector('{"queries": ["a"]}').config == {"queries": ["a"]}
    assert Connector(b'{"k": 1}').config == {"k": 1}
    assert Connector({"k": 2}).config == {"k": 2}
    assert Connector("pas du json").config == {}
    assert Connector(None).config == {}
