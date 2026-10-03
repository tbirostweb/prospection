"""Accès MySQL (PyMySQL). Connexion simple par process cron.

Placeholders : PyMySQL utilise le style pyformat (%s et %(name)s), identique
à ce que le pipeline emploie. Les colonnes JSON reçoivent des chaînes
json.dumps(...) et MySQL les parse automatiquement.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

import pymysql
from pymysql.cursors import DictCursor

from .config import DATABASE_URL


def _conn_params() -> dict:
    u = urlparse(DATABASE_URL)  # mysql://user:pass@host:port/db
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 3306,
        "user": u.username or "root",
        "password": u.password or "",
        "database": (u.path or "/").lstrip("/"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
        # Toutes les dates de la base sont en UTC naïf ; `first_seen_at DEFAULT CURRENT_TIMESTAMP` est comparé à
        # `UTC_TIMESTAMP()` : la session doit donc être en UTC, quel que soit le fuseau du serveur MySQL.
        "init_command": "SET time_zone = '+00:00'",
    }


@contextmanager
def connect() -> Iterator[pymysql.connections.Connection]:
    conn = pymysql.connect(**_conn_params())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(conn, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn, sql: str, params: tuple | dict = ()) -> int:
    """Exécute une requête. Renvoie lastrowid (utile pour les INSERT)."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid
