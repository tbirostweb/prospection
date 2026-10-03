"""Migrations DB incrémentales, jouées automatiquement au démarrage du worker.

Chaque fichier `db/updates/*.sql` est exécuté UNE seule fois, dans l'ordre
alphabétique, et enregistré dans la table `schema_migrations`. Rejouer un
déploiement ne réapplique pas un fichier déjà passé.

Ça complète le seed initial (`db/migrations/`, joué une seule fois par MySQL à
la création de la base) : ici on gère les évolutions APRÈS coup, sur une base
déjà en place.
"""
from __future__ import annotations

import os
import pathlib

import pymysql

from .config import log
from .db import _conn_params

MIGRATIONS_DIR = os.getenv("MIGRATIONS_DIR", "/app/db/updates")


def _split_statements(sql: str) -> list[str]:
    """Découpe un fichier SQL en instructions.

    Simple volontairement : retire les commentaires en ligne (`-- ...`) puis
    découpe sur `;`. Suffisant pour nos fichiers (pas de procédures stockées,
    pas de `;` dans les chaînes JSON). Ignore les fragments vides.
    """
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    cleaned = "\n".join(lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  filename VARCHAR(255) PRIMARY KEY,"
            "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        conn.commit()
        cur.execute("SELECT filename FROM schema_migrations")
        return {r["filename"] for r in cur.fetchall()}


def _ensure_state_table(conn) -> None:
    """Table d'état lisible par l'app (indicateur dans la page Sources)."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS migration_state ("
            "  id TINYINT PRIMARY KEY,"
            "  last_applied VARCHAR(255),"
            "  applied_count INT NOT NULL DEFAULT 0,"
            "  status VARCHAR(16) NOT NULL DEFAULT 'ok',"
            "  error TEXT,"
            "  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            "    ON UPDATE CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
    conn.commit()


def _record_state(conn, status: str, last_applied: str | None,
                  applied_count: int, error: str | None = None) -> None:
    """Écrit l'état courant. Appelé aussi APRÈS un rollback : c'est justement
    en cas d'échec que l'information doit remonter jusqu'à l'interface."""
    try:
        _ensure_state_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO migration_state (id, last_applied, applied_count, status, error)"
                " VALUES (1,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE last_applied=VALUES(last_applied),"
                " applied_count=VALUES(applied_count), status=VALUES(status),"
                " error=VALUES(error)",
                (last_applied, applied_count, status, (error or "")[:2000]),
            )
        conn.commit()
    except Exception:            # noqa: BLE001
        log.exception("[migrate] impossible d'enregistrer l'état")


def run() -> None:
    directory = pathlib.Path(MIGRATIONS_DIR)
    if not directory.is_dir():
        log.info("[migrate] aucun dossier %s, rien à faire.", MIGRATIONS_DIR)
        return

    files = sorted(p for p in directory.glob("*.sql"))
    if not files:
        log.info("[migrate] aucun fichier de migration.")
        return

    conn = pymysql.connect(**_conn_params())
    try:
        done = _applied(conn)
        _ensure_state_table(conn)
        pending = [f for f in files if f.name not in done]
        if not pending:
            log.info("[migrate] à jour (%d migration(s) déjà appliquée(s)).", len(done))
            _record_state(conn, "ok", max(done) if done else None, len(done))
            return

        for f in pending:
            statements = _split_statements(f.read_text(encoding="utf-8"))
            log.info("[migrate] application de %s (%d instructions)…", f.name, len(statements))
            current = ""
            try:
                with conn.cursor() as cur:
                    for i, stmt in enumerate(statements, 1):
                        current = f"#{i}: {stmt[:200]}"
                        cur.execute(stmt)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)", (f.name,)
                    )
                conn.commit()
                done.add(f.name)
                log.info("[migrate] %s appliquée.", f.name)
                _record_state(conn, "ok", f.name, len(done))
            except Exception as exc:
                conn.rollback()
                # On logue l'instruction fautive : sans ça, diagnostiquer une
                # migration en échec depuis les logs est un jeu de devinettes.
                log.error("[migrate] ÉCHEC sur %s\n  instruction %s\n  erreur : %s",
                          f.name, current, exc)
                log.error("[migrate] arrêt : les migrations suivantes sont reportées "
                          "au prochain déploiement (une fois le SQL corrigé).")
                _record_state(conn, "error", max(done) if done else None, len(done),
                              f"{f.name} · {current} · {exc}")
                raise
    finally:
        conn.close()


def status() -> None:
    """Affiche l'état des migrations (diagnostic en une commande)."""
    directory = pathlib.Path(MIGRATIONS_DIR)
    files = sorted(p.name for p in directory.glob("*.sql")) if directory.is_dir() else []
    print(f"Dossier    : {MIGRATIONS_DIR} ({'présent' if directory.is_dir() else 'ABSENT'})")
    print(f"Fichiers   : {len(files)} -> {', '.join(files) or '(aucun)'}")

    conn = pymysql.connect(**_conn_params())
    try:
        done = _applied(conn)
        pending = [f for f in files if f not in done]
        print(f"Appliquées : {len(done)} -> {', '.join(sorted(done)) or '(aucune)'}")
        print(f"En attente : {len(pending)} -> {', '.join(pending) or '(aucune)'}")
        row = None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM migration_state WHERE id=1")
                row = cur.fetchone()
        except Exception:        # noqa: BLE001
            pass
        if row:
            print(f"Dernier état : {row.get('status')} · {row.get('last_applied')} "
                  f"· {row.get('updated_at')}")
            if row.get("error"):
                print(f"Erreur      : {row['error']}")
        print("\nSources en base :")
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, connector, enabled, last_status FROM sources ORDER BY id")
            for s in cur.fetchall():
                print(f"  [{s['id']}] {s['name']} ({s['connector']}) "
                      f"{'actif' if s['enabled'] else 'inactif'} · {s['last_status']}")
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        status()
    else:
        run()
