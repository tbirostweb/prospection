# Image SearXNG avec la configuration du dépôt EMBARQUÉE (aucun montage de volume) :
# Dokploy n'applique pas les montages relatifs (./searxng), le settings.yml n'était donc pas lu
# et SearXNG tournait avec ses moteurs par défaut.
#
# Le fichier est copié HORS de /etc/searxng : ce dossier est déclaré VOLUME par l'image de base,
# et un volume anonyme déjà créé (ancien conteneur) masquerait le fichier copié. SEARXNG_SETTINGS_PATH
# indique à SearXNG où le lire. L'entrypoint de l'image crée toujours un settings.yml modèle dans
# /etc/searxng : il n'est PAS utilisé.
#
# Le secret n'est pas dans le dépôt : il vient de la variable d'environnement SEARXNG_SECRET
# (lue par SearXNG, prioritaire sur server.secret_key).
FROM searxng/searxng:latest

COPY --chown=977:977 settings.yml /usr/local/searxng/prospection/settings.yml

ENV SEARXNG_SETTINGS_PATH=/usr/local/searxng/prospection/settings.yml
