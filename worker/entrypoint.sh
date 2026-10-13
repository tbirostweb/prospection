#!/bin/sh
# Démarrage du worker : migrations (BLOQUANTES), vérification du schéma, recalcul des scores, puis planificateur NON root.
set -eu

APP_DIR="${APP_DIR:-/app}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
RUN_AS="${RUN_AS:-app}"

# 0) Lancé en root (cas Docker) : uniquement pour remettre le volume des journaux au bon propriétaire
#    (volumes existants créés du temps où le worker tournait en root), puis abandon DÉFINITIF des privilèges.
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$LOG_DIR"
  # Seulement si le dossier n'appartient pas encore à $RUN_AS (ancien volume root) : capacité requise CHOWN (puis SETUID/SETGID pour setpriv).
  if [ "$(stat -c %u "$LOG_DIR")" != "$(id -u "$RUN_AS")" ]; then
    chown -R "$RUN_AS:$RUN_AS" "$LOG_DIR"
  fi
  exec setpriv --reuid="$RUN_AS" --regid="$RUN_AS" --init-groups --no-new-privs "$0" "$@"
fi

umask 027
mkdir -p "$LOG_DIR"
chmod 750 "$LOG_DIR" 2>/dev/null || true     # propriétaire = utilisateur courant : aucune capacité nécessaire
cd "$APP_DIR"

# 1) Migrations DB incrémentales (db/updates/*.sql), jouées une seule fois chacune.
#    Un échec ARRÊTE le conteneur (code non nul) : aucune tâche ne tourne sur un schéma incomplet.
echo "[entrypoint] Migrations DB..."
if ! python -m worker.migrate >> "$LOG_DIR/worker.log" 2>&1; then
  echo "[entrypoint] ÉCHEC des migrations : arrêt du worker. Détail (fin du journal) :" >&2
  tail -n 20 "$LOG_DIR/worker.log" >&2 || true
  exit 1
fi
if ! python -m worker.migrate check >> "$LOG_DIR/worker.log" 2>&1; then
  echo "[entrypoint] Schéma incomplet après migration : arrêt du worker (voir $LOG_DIR/worker.log)." >&2
  exit 1
fi

# Scores recalculés avec les réglages en vigueur (sans réseau, quelques secondes) : non bloquant.
echo "[entrypoint] Recalcul des scores..."
python -m worker.local.rescore >> "$LOG_DIR/worker.log" 2>&1 || \
    echo "[entrypoint] recalcul des scores en échec (voir logs)."

# 2) Planificateur (remplace cron, sans root, sans écrire l'environnement sur disque). Cf. worker/scheduler.py.
echo "[entrypoint] Démarrage du planificateur."
exec python -m worker.scheduler
