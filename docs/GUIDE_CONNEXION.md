# Guide de connexion : moteurs de recherche, clés, vérification

Ce guide explique pas à pas comment brancher ce qui n'est pas actif par défaut : les clés d'API (Brave, Tavily, Serper), la migration de la
base, la vérification, le suivi de la consommation et l'import en masse. Pas besoin de savoir programmer : il suffit de copier les commandes.

> **Règle d'or : une clé d'API ne se met JAMAIS dans le dépôt Git (ni dans `.env.example`, ni dans un fichier commité), ni dans une
> conversation (chat, e-mail, ticket).** Elle se colle uniquement dans les variables d'environnement de Dokploy. Si une clé a fuité :
> supprime-la sur le site du fournisseur et crées-en une nouvelle.

Les tarifs et quotas cités ci-dessous sont ceux connus au moment de l'écriture : **vérifie-les sur le site de chaque fournisseur**, ils changent souvent.

---

## 1. Comprendre en 30 secondes : la recherche par paliers

Pour savoir si une entreprise a un site, le worker pose des questions à des moteurs de recherche, du gratuit au payant :

| Palier | Fournisseurs | Coût | Quand est-il utilisé ? |
|---|---|---|---|
| **0** | Moteurs de ton SearXNG (google, bing, brave, qwant…) | Gratuit | Toujours en premier. Le 2e moteur n'est interrogé que si le 1er n'a rien rendu. |
| **1** | API Brave Search, API Tavily | Gratuit dans la limite d'un quota | Seulement si **aucun** moteur SearXNG n'a pu répondre (bloqués, en pause, budget du jour atteint, SearXNG en panne). |
| **2** | API Serper (résultats Google) | **Payant** | En tout dernier recours, si les paliers 0 et 1 n'ont pas pu répondre. Budget plafonné. |

Sans clé, un palier est simplement ignoré : **rien de payant ne peut être appelé tant que `LOCAL_SERPER_API_KEY` est vide.**

Une réponse « aucun résultat » du gratuit ne déclenche pas le payant (sauf si tu mets `LOCAL_PAID_ON_EMPTY=1`).

---

## 2. Ce qui a changé / ce qui a été retiré

**Ajouté**
- Recherche par paliers (gratuit → API à quota → payant), avec budget par jour et par mois pour chaque fournisseur.
- Nouveaux fournisseurs facultatifs : **Tavily** (palier 1) et **Serper** (palier 2, payant).
- Compteur mensuel par fournisseur et plafond mensuel des dépenses (`LOCAL_PAID_MONTHLY_BUDGET`) : migration `db/updates/022_budget_moteurs_mensuel.sql`.
- `python -m worker.local.engines` affiche désormais les paliers, les budgets et la consommation du jour et du mois.
- Moins de requêtes par entreprise (objectif : environ 6 au lieu d'environ 12, **estimation non garantie**) : jusqu'à 5 noms de domaine devinés lus
  directement (sans moteur), stratégies réordonnées, requêtes en double supprimées, 2e moteur SearXNG seulement si le 1er ne rend rien.

**Modifié**
- Budget Brave par défaut : **30 requêtes / jour** (il était de 60).
- L'audit passif du site (SEO, technique, modernisation) est **désactivé par défaut** : `LOCAL_AUDIT=1` pour le réactiver.

**Retiré du worker de campagne** (le débit va aux fiches nouvelles : le but est de savoir si l'entreprise a un site et de trouver un contact)
- Mesures Lighthouse / PageSpeed.
- Approfondissement des prospects qualifiés (recherche élargie, dirigeant, chiffre d'affaires).
- Revérification des sites et rafraîchissement des fiches anciennes.
- Interrogation du BODACC entreprise par entreprise (la **veille** des nouvelles entreprises, elle, utilise toujours le BODACC).

**Variables qui ne servent plus** (tu peux les supprimer de Dokploy) : `LOCAL_PAGESPEED_PER_RUN`, `LOCAL_DEEP_PER_RUN`, `PAGESPEED_API_KEY`.

---

## 3. Créer les clés d'API

### 3.1 Serper (palier 2, payant) — recommandé comme filet de sécurité

1. Va sur **https://serper.dev** et crée un compte.
2. À l'inscription, environ **2 500 requêtes gratuites** sont offertes (à vérifier sur le site).
3. Ensuite, c'est payant : de l'ordre de **1 $ pour 1 000 requêtes** selon le pack acheté (à vérifier sur la page de tarifs).
4. Dans le tableau de bord, section **API Key**, copie la clé.
5. Tu la colleras dans la variable `LOCAL_SERPER_API_KEY` (étape 4).

Conseil : commence avec les crédits offerts et un plafond mensuel (`LOCAL_PAID_MONTHLY_BUDGET`, voir section 9) avant d'acheter un pack.

### 3.2 Brave Search API (palier 1)

1. Va sur **https://api-dashboard.search.brave.com** et crée un compte.
2. Il n'y a plus d'offre gratuite illimitée : Brave propose plutôt un **crédit mensuel offert (de l'ordre de 5 $ par mois)** puis une facturation
   à l'usage. **Vérifie l'offre en vigueur** et, si une carte bancaire est demandée, regarde s'il existe un plafond de dépense côté Brave.
3. Choisis l'offre, puis **API Keys → Add API Key** et copie la clé.
4. Tu la colleras dans `LOCAL_BRAVE_API_KEY`. Le budget par défaut (`LOCAL_BRAVE_DAILY_BUDGET=30`, soit environ 900 requêtes par mois) est
   pensé pour rester sous le crédit offert : ajuste-le si l'offre a changé.

### 3.3 Tavily (palier 1, facultatif)

1. Va sur **https://tavily.com**, crée un compte, copie la clé dans le tableau de bord.
2. Offre gratuite de l'ordre de **1 000 requêtes par mois** (à vérifier) : c'est la valeur par défaut de `LOCAL_TAVILY_MONTHLY_BUDGET`.
3. Tu la colleras dans `LOCAL_TAVILY_API_KEY`.

### 3.4 À savoir : Google Custom Search (CSE)

L'API Google Custom Search **ferme le 1er janvier 2027**. Le moteur « google cse » de SearXNG, s'il est actif chez toi, cessera alors de
répondre : ne compte pas dessus à long terme. Serper fournit des résultats Google par un autre chemin.

---

## 4. Où mettre les variables (Dokploy)

1. Ouvre Dokploy → ton projet → le service **Compose** de l'application.
2. Onglet **Environment**.
3. Ajoute une ligne par variable, au format `NOM=valeur`, par exemple :
   ```
   LOCAL_SERPER_API_KEY=colle_ta_clé_ici
   LOCAL_PAID_MONTHLY_BUDGET=2000
   ```
4. **Enregistre**, puis **redéploie** (bouton Deploy) : les variables ne sont lues qu'au démarrage des conteneurs.

> **Le préfixe `LOCAL_` est obligatoire.** Les tâches du worker sont lancées par `worker/scheduler.py`, qui ne leur transmet que les variables
> dont le nom commence par `LOCAL_` (plus quelques variables système). Une variable nommée `SERPER_API_KEY` ou `BRAVE_API_KEY` serait
> **ignorée sans message d'erreur**.

La liste complète et commentée des variables est dans [`.env.example`](../.env.example).

---

## 5. Appliquer la migration 022

La migration `db/updates/022_budget_moteurs_mensuel.sql` ajoute à la table `local_engine_health` le compteur mensuel et la date de la dernière
requête témoin. Elle est **appliquée automatiquement au démarrage du worker** (le redéploiement de l'étape 4 suffit).

Pour vérifier ou la relancer à la main (sans danger : chaque migration n'est jouée qu'une fois, et celle-ci est idempotente) :

```bash
docker compose exec worker python -m worker.migrate
```

Contrôle dans MySQL :
```sql
SHOW COLUMNS FROM local_engine_health LIKE 'month%';
```
Tu dois voir `month_start` et `month_requests`.

> Dans Dokploy, les commandes `docker compose exec …` se tapent dans le terminal du VPS, dans le dossier du projet ; tu peux aussi ouvrir
> directement le **terminal du conteneur `worker`** depuis Dokploy et taper seulement la partie `python -m …` (après `cd /app`).

---

## 6. Redéployer SearXNG et le worker

`searxng/settings.yml` est **embarqué dans l'image** du service `searxng` (construite par `searxng/Dockerfile`) : il n'y a plus de montage
de dossier (Dokploy n'applique pas les montages relatifs comme `./searxng`, le fichier n'était donc pas lu). Conséquence : après une
modification de `settings.yml`, un simple redémarrage ne suffit pas, il faut **reconstruire l'image**.

Dans Dokploy (application de type **Compose**, fichier `docker-compose.yml` à la racine du dépôt) :
1. Onglet **Environment** : vérifie que `SEARXNG_SECRET` est défini (longue chaîne aléatoire, par ex. `openssl rand -hex 32`). Il est
   **obligatoire** : sans lui, le déploiement s'arrête avec le message « SEARXNG_SECRET manquant ».
2. Pousse le dépôt, puis clique **Deploy** (ou **Rebuild**) : Dokploy reconstruit les images qui ont un `build:` (dont `searxng`, contexte
   `./searxng`) et recrée les conteneurs.

En ligne de commande, l'équivalent est :

```bash
docker compose up -d --build searxng
docker compose up -d worker      # recrée le worker avec les nouvelles variables
```

Pour vérifier que SearXNG lit bien le fichier du dépôt :
`docker compose exec searxng cat /usr/local/searxng/prospection/settings.yml`
(le fichier `/etc/searxng/settings.yml` créé par l'image est un modèle **inutilisé**, c'est normal).

---

## 7. Vérifier que tout est branché

```bash
docker compose exec worker python -m worker.local.engines
```

Ce que tu dois lire :
- **Paliers** : une ligne par palier. Pour Brave / Tavily / Serper : `ACTIF` si la clé est vue, sinon `inactif (… absente)`.
  Si tu as mis une clé et que tu lis « absente » : vérifie le nom exact de la variable (préfixe `LOCAL_`) et que tu as bien redéployé.
- **usage** : par fournisseur, requêtes des dernières 24 h, du mois, et fin de pause éventuelle (`cooldown`).
- **moteurs « general » actifs** : les moteurs que ton SearXNG utilise vraiment, puis pour chacun le nombre de résultats sur une requête test.
  `⚠ ne sert pas ce moteur` = le moteur demandé n'a pas répondu lui-même (désactivé ou bloqué).

### Quels moteurs SearXNG tester ?

Le fichier `searxng/settings.yml` active : **google, bing, qwant, yahoo, brave, duckduckgo** ; **google cse** est actif par défaut dans SearXNG
(fermeture prévue le 1er janvier 2027, voir plus haut). **mojeek et startpage ne sont pas utilisables** : les versions récentes de SearXNG les
marquent inactifs (CAPTCHA à preuve de travail) et on ne force pas leur activation.
« Activé » ne veut pas dire « fonctionne » : ces moteurs sont interrogés par lecture de leurs pages (scraping) et, depuis l'IP d'un VPS,
certains bloquent (CAPTCHA, « too many requests ») ou rendent 0 résultat. Le worker met alors le moteur en pause (cooldown) et continue avec
les autres : **des cooldowns dans `python -m worker.local.engines` sont normaux**, ce n'est pas une panne.

- Lance `python -m worker.local.engines` et regarde, moteur par moteur, le nombre de résultats.
- **Si bing, qwant, yahoo ou google n'apparaissent pas du tout dans la liste des moteurs actifs** (seulement brave, duckduckgo, google cse),
  SearXNG tourne encore avec sa configuration par défaut : l'image n'a pas été reconstruite. Redéploie le service `searxng` avec
  reconstruction (section 6) et vérifie le fichier avec `docker compose exec searxng cat /usr/local/searxng/prospection/settings.yml`.
- Un moteur qui rend 0 résultat de façon répétée est mis en pause automatiquement par le worker ; inutile de le retirer à la main.
- Pour forcer une liste précise (après tes tests) : `LOCAL_SEARCH_ENGINES=brave,bing,qwant` (par exemple). Vide = détection automatique.

---

## 8. Suivre la consommation et la santé (requêtes SQL)

Dans MySQL (Dokploy → service mysql → terminal, ou `docker compose exec mysql mysql -u… -p…`) :

État de chaque moteur (requêtes sur 24 h, pause, CAPTCHA, réponses vides d'affilée) :
```sql
SELECT engine, window_requests, cooldown_until, captchas, consecutive_empty FROM local_engine_health;
```

Consommation du mois (utile pour la dépense Serper) :
```sql
SELECT engine, month_start, month_requests FROM local_engine_health ORDER BY engine;
```

Historique des derniers passages (entreprises traitées, sites trouvés, nombre de recherches…) :
```sql
SELECT * FROM local_run_metrics ORDER BY id DESC LIMIT 50;
```
Le rapport `searches / processed` donne le nombre réel de requêtes par entreprise : c'est le vrai chiffre à surveiller (l'objectif d'environ 6
est une estimation).

---

## 9. Plafonner la dépense

- `LOCAL_SERPER_DAILY_BUDGET=150` : au plus 150 requêtes Serper par 24 h (par défaut).
- `LOCAL_PAID_MONTHLY_BUDGET` : plafond **mensuel** cumulé des fournisseurs payants. **Par défaut 0 = aucun plafond mensuel** (seul le plafond
  quotidien s'applique, soit jusqu'à environ 4 500 requêtes par mois). Pour être tranquille, mets une valeur, par exemple `2000`
  (≈ 2 $ par mois au tarif d'environ 1 $ / 1 000, à vérifier).
- `LOCAL_PAID_ON_EMPTY=0` (défaut) : le payant ne sert que quand le gratuit est indisponible. `1` = il sert aussi quand le gratuit ne trouve
  rien : plus de sites trouvés, mais plus de dépense.
- Mettre un budget quotidien à `0` désactive le fournisseur même si la clé est présente.
- Ajoute aussi, si le fournisseur le propose, une limite de dépense ou des alertes de facturation sur son propre tableau de bord.

---

## 10. Importer une grande zone (export SIRENE en masse)

Pour un département entier, l'export officiel est plus efficace que des milliers d'appels d'API.

1. Télécharge sur **data.gouv.fr** le jeu « Base Sirene des entreprises et de leurs établissements », fichier **`StockEtablissement_utf8`**
   (archive zip de plusieurs Go), puis décompresse-le pour obtenir `StockEtablissement_utf8.csv`.
2. Crée la campagne dans l'app et note son numéro (N).
3. Copie le fichier dans le conteneur worker (le dossier `/app/logs` est un volume persistant) :
   ```bash
   docker compose cp StockEtablissement_utf8.csv worker:/app/logs/
   ```
4. Lance l'import :
   ```bash
   docker compose exec worker python -m worker.local.bulk /app/logs/StockEtablissement_utf8.csv --campaign N
   ```
   (option `--limit 5000` pour plafonner). Le fichier est lu en flux, filtré (actifs, diffusables, département, activités), chaînes exclues,
   dédoublonné. Le worker enchaîne ensuite normalement.
5. Supprime le fichier ensuite pour libérer la place : `docker compose exec worker rm /app/logs/StockEtablissement_utf8.csv`.

---

## 11. OpenStreetMap : instance Overpass dédiée (facultatif)

Par défaut, le complément OSM interroge le serveur public `https://overpass-api.de/api/interpreter` (une requête par campagne). Si tu as ta
propre instance Overpass, ou si le serveur public est souvent saturé :

```
LOCAL_OSM_URL=https://ton-instance/api/interpreter
```

`LOCAL_OSM=0` désactive OSM.

---

## 12. Campagnes plus grandes

Une campagne traite **60 entreprises par défaut**. Dans le formulaire de campagne, tu peux monter le nombre maximum d'entreprises **jusqu'à
1 000**. Plus de fiches = plus de requêtes : monte progressivement et surveille `local_engine_health` (section 8). Le débit par passage se
règle avec `LOCAL_LOOKUPS_PER_RUN` (30 par défaut, toutes les 15 min) et `LOCAL_TIME_BUDGET_S` (600 s).

---

## 13. Réglages recommandés pour démarrer

À coller dans Dokploy (Environment), puis redéployer :

```
# Palier 0 : on garde la détection automatique et le rythme par défaut
LOCAL_ENGINE_DAILY_BUDGET=300
LOCAL_ENGINE_INTERVAL_S=3

# Palier 1 : Brave si tu as une clé (Tavily facultatif)
LOCAL_BRAVE_API_KEY=ta_clé_brave
LOCAL_BRAVE_DAILY_BUDGET=30

# Palier 2 : Serper, plafonné par jour ET par mois
LOCAL_SERPER_API_KEY=ta_clé_serper
LOCAL_SERPER_DAILY_BUDGET=150
LOCAL_PAID_MONTHLY_BUDGET=2000
LOCAL_PAID_ON_EMPTY=0

# Le but : site + contact. Audit seulement si tu en as besoin
LOCAL_AUDIT=0
```

Puis :
1. `docker compose exec worker python -m worker.local.engines` → tous les paliers voulus sont `ACTIF`.
2. Lance une petite campagne (60 entreprises).
3. Le lendemain, regarde `local_run_metrics` (requêtes par entreprise) et `month_requests` de `serper` : si la dépense reste faible et les
   moteurs gratuits suffisent, monte `max_companies` ou `LOCAL_LOOKUPS_PER_RUN`.
