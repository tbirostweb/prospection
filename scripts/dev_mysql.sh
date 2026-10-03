#!/bin/sh
# MySQL jetable pour les tests d'intégration (aucun Docker requis, ~30 Mo de RAM).
#   scripts/dev_mysql.sh start   -> démarre (initialise au 1er lancement) et affiche l'URL
#   scripts/dev_mysql.sh stop    -> arrête
#   scripts/dev_mysql.sh reset   -> arrête et SUPPRIME les données de test
# Requiert un binaire mysqld (brew install mysql). Données : $TMPDIR/prospection-mysql.
set -e
BASE="${TMPDIR:-/tmp}/prospection-mysql"
DATA="$BASE/data"
SOCK="/tmp/prospection-mysql.sock"     # chemin COURT : macOS limite les sockets à 103 caractères
PORT="${TEST_MYSQL_PORT:-33099}"
MYSQLD="$(command -v mysqld || ls /opt/homebrew/bin/mysqld /usr/local/bin/mysqld /usr/sbin/mysqld 2>/dev/null | head -1)"
MYSQLADMIN="$(dirname "$MYSQLD")/mysqladmin"

case "${1:-start}" in
  start)
    [ -x "$MYSQLD" ] || { echo "mysqld introuvable (brew install mysql)" >&2; exit 1; }
    mkdir -p "$BASE"
    if [ ! -d "$DATA/mysql" ]; then
      "$MYSQLD" --initialize-insecure --datadir="$DATA" --log-error="$BASE/init.err" >/dev/null 2>&1
    fi
    if ! [ -S "$SOCK" ]; then
      nohup "$MYSQLD" --datadir="$DATA" --port="$PORT" --bind-address=127.0.0.1 --socket="$SOCK" \
        --mysqlx=OFF --innodb-buffer-pool-size=32M --performance-schema=OFF --skip-log-bin \
        --log-error="$BASE/mysql.err" --pid-file="$BASE/mysql.pid" >/dev/null 2>&1 &
      for i in $(seq 1 60); do [ -S "$SOCK" ] && break; sleep 1; done
    fi
    [ -S "$SOCK" ] || { echo "MySQL n'a pas démarré (voir $BASE/mysql.err)" >&2; exit 1; }
    echo "mysql://root@127.0.0.1:$PORT" ;;
  stop)
    "$MYSQLADMIN" -uroot -S "$SOCK" shutdown 2>/dev/null || true
    for i in $(seq 1 30); do [ -S "$SOCK" ] || break; sleep 1; done ;;
  reset)
    "$0" stop; rm -rf "$BASE" "$SOCK"; echo "données de test supprimées" ;;
  *) echo "usage : $0 start|stop|reset" >&2; exit 2 ;;
esac
