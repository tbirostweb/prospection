# Sécurité, exploitation et données personnelles

Mesures en place dans le code, et ce qui reste à décider ou à prouver par l'exploitant / l'éditeur.
Les mentions **[À FOURNIR]** sont des informations que le code ne peut pas inventer.

## 1. Accès à l'application

- Toutes les pages et API sont protégées (`web/middleware.ts`). Navigateur : page `/login` (formulaire, identifiants
  `APP_USER`/`APP_PASSWORD`) qui pose un cookie de session signé HMAC-SHA256 (`HttpOnly`, `Secure` en production, `SameSite=Lax`,
  30 jours ; clé dérivée de `APP_PASSWORD`, ou de `APP_SESSION_SECRET` facultatif ≥ 32 caractères) ; déconnexion par le menu.
  Repli : un en-tête `Authorization: Basic` valide reste accepté (healthcheck `web/healthcheck.js`, scripts `curl -u`).
  Sans authentification : page → redirection `/login?next=…` (chemin interne uniquement), API → 401 JSON.
  Fail-closed si `APP_USER`/`APP_PASSWORD` absents ou si `APP_PASSWORD` fait moins de **14 caractères**. Comparaison en temps constant.
- Limitation des échecs par adresse IP (formulaire et Basic) : 10 échecs / 15 min → **429** pendant 15 min (`AUTH_MAX_FAILURES`, `AUTH_BLOCK_MINUTES`).
  Mémoire bornée, par instance : avec plusieurs instances web, ajouter une limite au proxy (Traefik `ratelimit`).
- Plafond GLOBAL, tous clients confondus : 50 échecs / 15 min → **429** pour tout le monde pendant 15 min (X-Real-Ip est falsifiable depuis un conteneur du réseau partagé `dokploy-network`). Voir README → « Durcir le proxy ».
- Anti-CSRF sur toutes les mutations : `Origin` obligatoire et identique à l'hôte (ou `APP_URL`), `Sec-Fetch-Site` cross-site refusé,
  `Content-Type: application/json` exigé pour POST/PUT/PATCH (403 / 415).
- **Second facteur : [À DÉCIDER par l'exploitant]**. La connexion par mot de passe seul ne permet pas de 2FA. Placer devant le service `web`
  un proxy d'authentification (Traefik `forwardAuth` + Authelia/Authentik, ou un accès de type Zero Trust) avec 2FA/passkey,
  puis tester : anonyme → refus, mauvais facteur → refus, session expirée → réauthentification.
- Révocation : changer `APP_PASSWORD` dans Dokploy puis redéployer ; toutes les sessions sont alors invalidées (le mot de passe entre dans la clé de signature) : vérifier le retour à `/login`.

## 2. En-têtes HTTP

`web/next.config.js` (source testée : `web/lib/security.ts`) : CSP (`frame-ancestors 'none'`, `object-src 'none'`,
scripts `self` + cdnjs pour Leaflet avec SRI), HSTS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` (aucun référent vers les sites tiers ; Origin réel sur les formulaires de connexion),
`Permissions-Policy`. `'unsafe-inline'` reste nécessaire aux scripts d'hydratation Next (amélioration possible : nonce).
Contrôle en production : `curl -I https://<DOMAINE>/` et `curl -I https://<DOMAINE>/page-inexistante`.

## 3. Conteneurs

- `web` : Node 24 LTS, `npm ci`, utilisateur `node` (UID 1000), `cap_drop: ALL`, `no-new-privileges`, healthcheck réel (`/api/health`, base joignable).
- `worker` : utilisateur `app` (UID 10001), planificateur Python (plus de `cron` root ni de crontab contenant les secrets),
  `cap_drop: ALL`, dépendances verrouillées par empreintes (`worker/requirements.lock`). Migration en échec = conteneur arrêté.
- Réseaux : `db` interne sans Internet (mysql, web, worker) ; `egress` (worker, searxng).
- Images épinglées par digest. Mise à jour : `docker buildx imagetools inspect <image:tag>`, remplacer le digest, rebuild, tests.
- **MySQL 8.0 : fin de support (avril 2026)**. Montée vers 8.4 LTS à planifier : sauvegarde complète (`mysqldump --single-transaction --routines`),
  restauration testée sur une copie isolée, attention à `--default-authentication-plugin=mysql_native_password` (option supprimée en 8.4 et plugin désactivé par défaut :
  migrer l'utilisateur applicatif vers `caching_sha2_password`), puis bascule et contrôle applicatif.
- Régénérer le verrou Python : `cd worker && pip-compile --generate-hashes --strip-extras -o requirements.lock pyproject.toml`.

## 4. Telegram

Refus par défaut : chat `TELEGRAM_CHAT_ID` obligatoire et identique, auteur autorisé (`TELEGRAM_ALLOWED_USER_IDS`, sinon propriétaire
du chat privé, sinon administrateur du canal). Offset enregistré atomiquement avant chaque action : jamais de double application.

## 5. Données personnelles (prospection B2B)

Traitement : entreprises et contacts professionnels issus de sources publiques, scoring, historique de prospection, opposition.

| Donnée | Durée | Mécanisme |
| --- | --- | --- |
| Prospect jamais travaillé | 18 mois après découverte | `worker.local.retention` (quotidien 04:30) |
| Prospect exclu / écarté | 6 mois | idem |
| Prospect travaillé (hors « Gagné ») | 36 mois après la collecte ou la dernière réponse enregistrée du prospect (`last_prospect_contact_at`, voir « Limites de minimisation restantes ») | idem (`LOCAL_RETENTION_WORKED_MONTHS`) |
| Prospect « Gagné » (client) | **[À FIXER par l'éditeur]** (relation contractuelle) | non purgé automatiquement |
| Résultats d'apprentissage | anonymisés 36 mois après, si le prospect n'existe plus | idem |
| Liste « ne plus contacter » | conservée (données minimales nécessaires au respect de l'opposition) | — |
| Cache API / journal d'erreurs / métriques | 60 jours / 180 jours / 1 an | idem |
| Journaux du worker | 5 000 dernières lignes par fichier | `worker/scheduler.py` |
| Sauvegardes | **[À FIXER par l'exploitant]** | hors dépôt |

Droits des personnes (accès, portabilité, effacement, opposition), après vérification proportionnée de l'identité :
`docker compose exec worker python -m worker.local.privacy export --siret <SIRET>` / `erase --siret <SIRET>` (voir l'aide du module).

Information des personnes (art. 14 RGPD) : chaque brouillon contient la source des coordonnées et le lien de la notice
(Réglages → « Mon identité » → lien de la notice). Sans ce lien, le brouillon porte un marqueur et l'ouverture dans la messagerie est désactivée.

**[À FOURNIR par l'éditeur]** :
- la notice d'information elle-même (identité et coordonnées du responsable, finalité, base légale — intérêt légitime pour une prospection
  B2B en rapport avec l'activité de la personne —, catégories de données, sources, destinataires, durées, droits, réclamation CNIL) et son URL publique ;
- le registre des traitements, l'analyse de la nécessité d'une AIPD (scoring / profilage) et la désignation éventuelle d'un DPO ;
- la liste des sous-traitants et transferts avec leurs garanties (hébergeur du VPS, Telegram, et selon configuration : Brave, Tavily, Serper, Google PageSpeed) ;
- la procédure de violation de données (documentation interne, notification CNIL si risque, si possible sous 72 h ; information des personnes si risque élevé).

Destinataires techniques observés dans le code (appels sortants) : recherche-entreprises.api.gouv.fr, geo.api.gouv.fr,
bodacc-datadila.opendatasoft.com, overpass-api.de, moteurs via SearXNG, api.telegram.org, et si des clés sont configurées
api.search.brave.com, api.tavily.com, google.serper.dev, www.googleapis.com (PageSpeed). Côté navigateur : cdnjs.cloudflare.com (Leaflet),
tile.openstreetmap.org (fonds de carte), geo.api.gouv.fr (recherche de commune). Stockage local du navigateur : préférence de carte
(centre/rayon), strictement fonctionnel ; aucun outil de mesure d'audience.

### Inventaire technique vérifié dans le code (faits uniquement)

- **Champs collectés par le worker** (`local_prospects`, migration 015) : SIREN/SIRET, raison sociale, adresse, coordonnées GPS, NAF, forme juridique,
  effectif, site web, téléphone, e-mail et son type, page source du contact, scores. Le **nom du dirigeant** (personne physique, registre officiel)
  est enregistré dans `manager_name` (`worker/worker/local/sirene.py`, `store.py`) ; seuls les établissements actifs et diffusables sont retenus.
- **Telegram** : les alertes prospect contiennent le nom de l'entreprise, l'activité, la ville et des indicateurs « email disponible sur la fiche »
  / « téléphone disponible sur la fiche », sans l'adresse ni le numéro (`worker/worker/notify.py`) ; le message de veille contient des indicateurs ☎/✉
  (`worker/worker/local/notify.py`) ; le résumé quotidien contient un indicateur « ☎ appeler », sans le numéro, et un lien d'itinéraire (`worker/worker/digest.py`).
- **E-mail** : aucun envoi applicatif, aucun SMTP configuré ; brouillons ouverts par `mailto:` dans la messagerie de l'utilisateur.
- **Cookies** : aucun cookie posé par l'application. **Stockage navigateur** : une seule entrée `localStorage` (centre et rayon de la carte, `MapView.tsx`).
- **Mesure d'audience** : aucune.
- **Polices** : IBM Plex via `next/font/google`, servies par l'application (CSP `font-src 'self'`).
- **Licences** : aucun fichier LICENSE dans le dépôt. Dépendances directes web : MIT / Apache-2.0 (`node_modules`) ; Python : httpx BSD-3-Clause, PyMySQL et selectolax MIT.
  Données cartographiques : attribution OpenStreetMap affichée sur la carte.
- **Hébergeur du VPS, pays des sous-traitants, base légale, durées retenues** : non déterminables par le code (voir les marqueurs [À FOURNIR] ci-dessus).

## 6. Preuves à fournir par l'exploitant (hors dépôt)

Pare-feu et ports, SSH (clés, root/mot de passe désactivés, accès de secours), versions OS/Docker, TLS et redirection HTTP→HTTPS,
sauvegardes chiffrées hors hôte et **restauration testée** (RPO/RTO), privilèges du compte MySQL applicatif (limité à sa base),
limites CPU/RAM, supervision et alertes reçues. Commandes de contrôle : `scripts/vps_check.sh` et l'annexe d'infrastructure de l'audit.

### Limites de minimisation restantes

Les règles de 18 mois (jamais travaillé) et 6 mois (exclu / écarté) partent de `discovered_at`. La règle de 36 mois des prospects travaillés
part de `LAST_ACTIVITY` (`worker/worker/local/retention.py`) = la plus récente de `discovered_at` et `last_prospect_contact_at`. Cette colonne
(migration additive `db/updates/024_prospect_contact_retention.sql`) est horodatée par l'API quand une réponse entrante est saisie
(`REPLIED`, `INTERESTED`, `NOT_INTERESTED`, `WON`) ; relances, brouillons et notes internes ne repoussent plus l'échéance. Les réponses
antérieures à la migration n'ont pas de date fiable : la colonne reste NULL (aucune date reconstituée) et l'échéance repart de la collecte,
ce qui peut purger un prospect travaillé collecté il y a plus de 36 mois même s'il a répondu avant la migration. Ressaisir la réponse
avant le premier passage de la purge si ce cas existe. Une ressaisie de la même réponse remet l'horodatage à la date du jour.
`local_prospects` n'a pas de colonne `updated_at` ; l'anonymisation de `local_outcomes` part toujours d'une date interne
(`COALESCE(contacted_at, updated_at)`). Ce n'est donc PAS une mise en conformité démontrée. Aucune purge n'a été lancée
sur une base réelle lors de cette correction locale.

Leaflet reste chargé depuis cdnjs avec SRI : aucune dépendance Leaflet locale n’est disponible dans le projet. Son auto-hébergement reste à réaliser avec une version et une licence conservées. Les tuiles OpenStreetMap restent un service externe distinct.
