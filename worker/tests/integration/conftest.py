"""Tests d'intégration sur un VRAI MySQL (les mocks ne voient pas les erreurs de SQL — 3 déploiements cassés l'ont prouvé).

Activés seulement si TEST_DATABASE_URL est défini (ex. `mysql://root@127.0.0.1:33099`, voir
`scripts/dev_mysql.sh start`). Sinon, tout le dossier est ignoré : la suite unitaire reste sans dépendance.

La base est construite UNE fois comme en production : 001 (schéma) + 002 (seed) puis toutes les migrations
`db/updates/*`. Entre deux tests, les données sont vidées (les sources sont remplacées par des faux connecteurs).
Réseau et Telegram sont toujours simulés.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from urllib.parse import urlparse

import pymysql
import pytest
from pymysql.constants import CLIENT

from worker import db

ROOT = pathlib.Path(__file__).resolve().parents[3]
BASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not BASE_URL, reason="TEST_DATABASE_URL non défini (voir scripts/dev_mysql.sh)")


def pytest_collection_modifyitems(config, items):
    if BASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL non défini (voir scripts/dev_mysql.sh)")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


def _admin(db: str | None = None):
    u = urlparse(BASE_URL)
    return pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username or "root",
                           password=u.password or "", database=db, charset="utf8mb4", autocommit=True,
                           client_flag=CLIENT.MULTI_STATEMENTS)


def _run_file(conn, path: pathlib.Path) -> None:
    with conn.cursor() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
        while cur.nextset():
            pass


def build_database(name: str, updates_dir: pathlib.Path | str | None = None) -> str:
    """Crée `name` : schéma + seed + migrations (celles de `updates_dir`, par défaut toutes). Renvoie son URL."""
    from worker import db, migrate
    with _admin() as c:
        with c.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    with _admin(name) as c:
        for f in sorted((ROOT / "db" / "migrations").glob("*.sql")):
            _run_file(c, f)
    u = urlparse(BASE_URL)
    url = f"mysql://{u.username or 'root'}{':' + u.password if u.password else ''}@{u.hostname}:{u.port or 3306}/{name}"
    return url


@pytest.fixture(scope="session")
def database_url():
    name = f"prospection_it_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    url = build_database(name)
    yield url
    with _admin() as c:
        with c.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")


@pytest.fixture(scope="session")
def migrated(database_url):
    """Applique les migrations `db/updates` UNE fois (comme au démarrage du worker)."""
    import worker.db as wdb
    import worker.migrate as wmig
    old = (wdb.DATABASE_URL, wmig.MIGRATIONS_DIR)
    wdb.DATABASE_URL, wmig.MIGRATIONS_DIR = database_url, str(ROOT / "db" / "updates")
    try:
        wmig.run()
    finally:
        wdb.DATABASE_URL, wmig.MIGRATIONS_DIR = old
    return database_url


class Telegram:
    """Faux Telegram : enregistre au lieu d'envoyer."""

    def __init__(self):
        self.prospects: list[tuple] = []      # (prospect, issues, summary)
        self.texts: list[str] = []
        self.ok = True

    def send_prospect(self, p, issues=None, summary=None):
        if self.ok:
            self.prospects.append((p, issues, summary))
        return self.ok

    def send_text(self, text, disable_preview=True, reply_markup=None):
        self.texts.append(text)
        return self.ok


@pytest.fixture
def telegram(monkeypatch):
    from worker import alerts, notify
    tg = Telegram()
    monkeypatch.setattr(notify, "send_prospect", tg.send_prospect)
    monkeypatch.setattr(notify, "send_text", tg.send_text)
    monkeypatch.setattr(alerts, "STATE_FILE", "/nonexistent/alert_state.json")   # pas de throttle persistant
    monkeypatch.setattr(alerts, "_load", lambda: {})
    monkeypatch.setattr(alerts, "_save", lambda state: None)
    return tg


@pytest.fixture
def no_network(monkeypatch):
    """Aucune requête sortante : `net.try_get` renvoie None (site injoignable). Les tests qui ont
    besoin d'une page la fournissent eux-mêmes."""
    from worker import net
    from worker.local import sitefinder
    monkeypatch.setattr(net, "try_get", lambda *a, **k: None)
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda domains, timeout=4.0: [])   # aucun domaine deviné n'existe


@pytest.fixture
def env_sans_telegram(migrated, monkeypatch, tmp_path, no_network):
    """Base propre + réseau simulé, SANS faux Telegram (les tests HTTP réels branchent le leur)."""
    import worker.db as wdb
    monkeypatch.setattr(wdb, "DATABASE_URL", migrated)
    monkeypatch.setenv("LOCAL_LOCK", str(tmp_path / "local.lock"))
    with _admin(urlparse(migrated).path.lstrip("/")) as c:
        with c.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in ("local_prospect_sources", "local_prospect_campaigns", "local_prospects", "local_campaigns", "local_do_not_contact", "local_http_cache",
                      "local_engine_health", "local_events", "local_run_metrics", "local_bad_sites", "local_site_feedback", "local_health_report",
                      "local_outcomes"):
                try:
                    cur.execute(f"DELETE FROM `{t}`")
                except pymysql.err.ProgrammingError:
                    pass                        # table absente : pas ce test qui la vérifie
            cur.execute("DELETE FROM settings WHERE skey = 'local_learning'")       # modèle appris : propre à chaque test
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
    return migrated


@pytest.fixture
def env(env_sans_telegram, telegram):
    """Base propre + composants simulés (Telegram, réseau). Renvoie l'URL ; `conn()` ouvre une connexion."""
    return env_sans_telegram


@pytest.fixture
def conn(env):
    from worker import db
    with db.connect() as c:
        c.autocommit(True)          # lit toujours l'état courant (pas d'instantané de transaction périmé)
        yield c


@pytest.fixture(autouse=True)
def _no_real_osm(monkeypatch):
    """Aucun test ne doit joindre le vrai Overpass : OSM est désactivé sauf dans les tests qui l'injectent explicitement."""
    from worker.local import osm
    monkeypatch.setattr(osm, "ENABLED", False)
