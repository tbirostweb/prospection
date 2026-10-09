#!/bin/sh
set -e

mkdir -p /app/logs
cd /app

# 1) Migrations DB incrémentales (db/updates/*.sql), jouées une seule fois chacune.
echo "[entrypoint] Migrations DB..."
python -m worker.migrate >> /app/logs/worker.log 2>&1 || \
    echo "[entrypoint] migrations en échec (voir logs)."

# Scores recalculés avec les réglages en vigueur (sans réseau, quelques secondes) :
# après un changement de poids ou de seuils, l'application est à jour dès le redéploiement.
echo "[entrypoint] Recalcul des scores..."
python -m worker.local.rescore >> /app/logs/worker.log 2>&1 || \
    echo "[entrypoint] recalcul des scores en échec (voir logs)."

# 2) Génère la crontab AVEC l'environnement courant.
#    IMPORTANT : cron ne transmet pas les variables d'env du conteneur à ses
#    tâches. On les inscrit en tête de crontab pour que le pipeline retrouve
#    DATABASE_URL, SEARXNG_URL, etc. (sinon il plante sans pouvoir se connecter).
{
  printenv | grep -E '^(DATABASE_URL|SEARXNG_URL|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|APP_URL|TZ|LOG_LEVEL|MIGRATIONS_DIR|PAGESPEED_API_KEY|LOCAL_[A-Z_]+)='
  # Boutons Telegram (⭐ 📞 🚫 ❌) : relevé toutes les 5 min, 24h/24 — tu dois pouvoir
  # classer depuis ton téléphone à tout moment.
  echo "*/5 * * * * cd /app && /usr/local/bin/python -m worker.telegram_poll >> /app/logs/telegram.log 2>&1"
  # Prospection locale : reprend les campagnes en attente, par étapes bornées. Sans campagne, sort aussitôt.
  echo "*/15 6-22 * * * cd /app && /usr/local/bin/python -m worker.local.run >> /app/logs/local.log 2>&1"
  # Veille des nouvelles entreprises (BODACC + SIRENE) pour les campagnes où elle est activée, chacune à son rythme (7 ou 30 jours).
  echo "10 7 * * * cd /app && /usr/local/bin/python -m worker.local.watch >> /app/logs/local.log 2>&1"
  # Surveillance des dégradations (moteurs, taux de sites trouvés, sources) : alerte Telegram throttlée.
  echo "* * * * * cd /app && /usr/local/bin/python -m worker.reset --pending >> /app/logs/local.log 2>&1"
  echo "5 * * * * cd /app && /usr/local/bin/python -m worker.local.monitor >> /app/logs/local.log 2>&1"
  echo "15 7 * * * cd /app && /usr/local/bin/python -m worker.digest daily >> /app/logs/digest.log 2>&1"
  echo "0 8 * * 1 cd /app && /usr/local/bin/python -m worker.digest weekly >> /app/logs/digest.log 2>&1"
  echo "0 4 * * * for f in /app/logs/*.log; do tail -n 5000 \"\$f\" > \"\$f.tmp\" && mv \"\$f.tmp\" \"\$f\"; done"
} | crontab -
echo "[entrypoint] Crontab installée."

echo "[entrypoint] Démarrage de cron."
cron -f
