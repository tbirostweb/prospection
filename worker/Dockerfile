# Image épinglée par digest : mise à jour volontaire (`docker buildx imagetools inspect python:3.11-slim`, rebuild + tests).
FROM python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b

WORKDIR /app

# Dépendances système minimales (tzdata pour que le planificateur respecte l'heure de Paris).
# Plus de `cron` : le planificateur Python tourne sans privilèges (worker/scheduler.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Fuseau horaire : les tâches (résumé 07:15, etc.) tournent à l'heure française.
ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Utilisateur d'exécution non privilégié.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

# NOTE : le contexte de build est la RACINE du dépôt (cf. docker-compose.yml),
# afin de pouvoir embarquer db/updates dans l'image.
# Dépendances : versions exactes et empreintes vérifiées (requirements.lock, généré par pip-compile --generate-hashes).
COPY worker/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Code exécuté depuis /app (`python -m worker.…`) : pas d'installation du paquet, donc aucun outil de build téléchargé sans empreinte.
COPY worker/ ./

# Migrations SQL embarquées DANS l'image (et non via un bind mount : certains
# orchestrateurs, dont Dokploy, n'appliquent pas les montages relatifs, ce qui
# laissait /app/db/updates vide et empêchait toute migration de s'exécuter).
COPY db/updates ./db/updates

RUN mkdir -p /app/logs && chown app:app /app/logs && chmod 750 /app/logs
COPY worker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

# Exécution NON root (UID 10001), y compris pour `docker compose exec worker …`. La propriété d'un ancien volume
# /app/logs (créé du temps où le worker tournait en root) est réparée par le service `worker-init` (docker-compose.yml) ;
# lancé hors compose en root, entrypoint.sh répare lui-même puis abandonne les privilèges (setpriv).
USER app
CMD ["/entrypoint.sh"]
