FROM python:3.11-slim

WORKDIR /app

# Dépendances système minimales (tzdata pour que cron respecte l'heure de Paris)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Fuseau horaire : le cron (résumé 08:00, etc.) tourne à l'heure française.
ENV TZ=Europe/Paris
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# NOTE : le contexte de build est la RACINE du dépôt (cf. docker-compose.yml),
# afin de pouvoir embarquer db/updates dans l'image.
COPY worker/pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY worker/ ./

# Migrations SQL embarquées DANS l'image (et non via un bind mount : certains
# orchestrateurs, dont Dokploy, n'appliquent pas les montages relatifs, ce qui
# laissait /app/db/updates vide et empêchait toute migration de s'exécuter).
COPY db/updates ./db/updates

# La crontab est générée au démarrage par entrypoint.sh (elle a besoin des
# variables d'environnement du conteneur, que cron ne transmet pas seul, et de
# la fenêtre horaire PIPELINE_CRON). Cf. worker/entrypoint.sh.
RUN mkdir -p /app/logs
COPY worker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
