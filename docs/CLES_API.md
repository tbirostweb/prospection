# Clés d'API et accès : où les créer, quoi en faire

Ce guide recense **tout ce que l'application peut utiliser** (clés, comptes, mots de passe, adresses de services), dit pour chacun où aller,
comment obtenir la valeur, dans quelle variable la coller, et comment vérifier que ça marche. Pas besoin de savoir programmer.

- Tarifs et quotas **vérifiés le 02/10/2026** sur les sites des fournisseurs. Ils changent souvent : revérifie avant de payer quoi que ce soit.
- Ce qui n'a pas pu être vérifié est marqué **« à confirmer »**.
- Seules les variables qui existent réellement dans le code sont citées. La liste commentée complète est dans [`.env.example`](../.env.example) ;
  le détail du fonctionnement de la recherche par paliers est dans [`GUIDE_CONNEXION.md`](GUIDE_CONNEXION.md), celui de Telegram dans
  [`../TELEGRAM.md`](../TELEGRAM.md).

---

## 1. Tableau récapitulatif

| Service / accès | À quoi ça sert dans l'app | Coût (au 02/10/2026) | Variable(s) | Obligatoire ? |
|---|---|---|---|---|
| **Accès à l'app web** (HTTP Basic Auth) | Protège toutes les pages ; sans ces deux valeurs, l'accès est refusé à tout le monde | Gratuit (tu choisis les valeurs) | `APP_USER`, `APP_PASSWORD` | **Oui** |
| **Base MySQL** (conteneur `mysql`) | Stocke campagnes, prospects, compteurs | Gratuit (tu choisis les mots de passe) | `MYSQL_ROOT_PASSWORD`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `DATABASE_URL` | **Oui** |
| **SearXNG** (conteneur `searxng`) | Palier 0, gratuit : recherche du site officiel des entreprises | Gratuit | `SEARXNG_URL`, `SEARXNG_SECRET` | **Oui** |
| **Domaine** | Adresse publique de l'app (Traefik / Let's Encrypt via Dokploy) | Prix de ton nom de domaine | `DOMAIN`, `APP_URL` (facultatif) | `DOMAIN` oui |
| **Telegram** (bot) | Alertes « À contacter », boutons ⭐ 📞 🚫 ❌, résumé quotidien et hebdo, alertes de dégradation | Gratuit | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Non (fortement conseillé) |
| **Serper** (résultats Google) | Palier 2, **payant**, dernier recours pour trouver un site | 2 500 requêtes offertes, puis packs prépayés dès 50 $ (50 000 req., ≈ 1 $ / 1 000, valables 6 mois) | `LOCAL_SERPER_API_KEY` (+ budgets) | Non |
| **Brave Search API** | Palier 1, secours à quota | 5 $ de crédit offert / mois = ≈ 1 000 requêtes, puis 5 $ / 1 000 ; **carte bancaire exigée** | `LOCAL_BRAVE_API_KEY` (+ budget) | Non |
| **Tavily** | Palier 1, secours à quota | 1 000 crédits gratuits / mois, sans carte (1 recherche « basic » = 1 crédit) | `LOCAL_TAVILY_API_KEY` (+ budgets) | Non |
| **API Recherche d'entreprises** (data.gouv / DINUM) | Découverte des entreprises autour d'un point (données SIRENE) | Gratuit, **sans clé ni compte** (limite 7 appels/s) | aucune | Utilisée automatiquement |
| **Export SIRENE en masse** (data.gouv.fr) | Import d'un département entier (`python -m worker.local.bulk`) | Gratuit, sans compte | aucune | Non |
| **geo.api.gouv.fr**, **BODACC** | Géocodage des villes ; veille des nouvelles entreprises | Gratuit, sans clé | aucune | Utilisées automatiquement |
| **OpenStreetMap / Overpass** | Complément : site, téléphone, e-mail publics | Gratuit, sans clé | `LOCAL_OSM`, `LOCAL_OSM_URL` | Non (actif par défaut) |
| **PageSpeed Insights** (Google) | **Plus utilisé** : le code existe mais n'est appelé nulle part | Gratuit | `PAGESPEED_API_KEY` | **Non — ne pas créer** |

Il n'y a **pas** d'envoi d'e-mail (aucun SMTP dans le code), **pas** de clé INSEE (l'app n'utilise pas l'API Sirene de l'INSEE, seulement l'API
Recherche d'entreprises et l'export data.gouv), et **pas** de compte Google Maps.

---

## 2. Ordre conseillé

1. **Accès à l'app** (`APP_USER` / `APP_PASSWORD`), **mots de passe MySQL** et **`SEARXNG_SECRET`** : indispensables avant le premier déploiement.
2. **Telegram** : gratuit, 10 minutes, et c'est par là que l'app te prévient (prospects, pannes de moteurs).
3. **Serper** : filet de sécurité quand les moteurs gratuits sont bloqués. Les 2 500 requêtes offertes suffisent pour démarrer, sans carte.
4. **Tavily** : gratuit, sans carte, 1 000 recherches par mois.
5. **Brave Search API** : utile, mais demande une carte bancaire ; à faire en dernier, avec une limite de dépense.
6. Rien à créer pour SIRENE, geo.api.gouv.fr, BODACC et OSM. PageSpeed : ne rien faire.

> Remarque : dans le code, Serper est interrogé **après** Brave et Tavily (c'est le seul payant). « 3. Serper » ci-dessus est l'ordre dans
> lequel **créer** les comptes, pas l'ordre d'appel.

### Coût mensuel maximum si tout est activé (réglages par défaut)

| Fournisseur | Plafond appliqué par l'app | Coût maximum |
|---|---|---|
| Brave | `LOCAL_BRAVE_DAILY_BUDGET=30` → au plus ≈ 930 requêtes sur un mois de 31 jours | **0 $** (sous les 5 $ offerts), à condition que l'offre ne change pas |
| Tavily | `LOCAL_TAVILY_MONTHLY_BUDGET=1000` | **0 $** sur l'offre gratuite (les requêtes s'arrêtent au quota) |
| Serper | `LOCAL_SERPER_DAILY_BUDGET=150` → jusqu'à ≈ 4 650 requêtes / mois si `LOCAL_PAID_MONTHLY_BUDGET=0` (défaut) | ≈ **4,65 $ de crédits consommés / mois** au pire ; avec `LOCAL_PAID_MONTHLY_BUDGET=2000` : ≈ **2 $ / mois** |
| Telegram, SIRENE, OSM, SearXNG | — | 0 $ |

**Total : 0 $ tant que les 2 500 requêtes Serper offertes durent, puis au maximum ≈ 2 à 5 $ de consommation par mois.** Attention : chez Serper on
n'achète pas « au mois » mais des **packs prépayés** ; le plus petit coûte **50 $** (valable 6 mois). C'est donc une dépense ponctuelle de 50 $ au
maximum par période de 6 mois, dont une partie peut expirer sans être utilisée. Conseil : mets `LOCAL_PAID_MONTHLY_BUDGET=2000`.

---

## 3. Règles de sécurité (à lire avant tout)

- **Une clé ne va jamais dans le dépôt Git** : ni dans `.env.example`, ni dans un fichier commité, ni dans un commentaire de code.
- **Jamais dans un chat, un e-mail, un ticket ou une capture d'écran** (y compris une conversation avec une IA). Masque la valeur avant toute
  capture du tableau de bord Dokploy ou du fournisseur.
- **Un seul endroit** : l'onglet **Environment** de Dokploy (voir section 4).
- **Si une clé a fuité** (ou si tu as un doute) : supprime-la (« revoke » / « delete ») sur le site du fournisseur, crée-en une nouvelle,
  remplace-la dans Dokploy, redéploie. Pour Telegram : `/revoke` auprès de @BotFather. Pour un mot de passe : change-le partout où il apparaît.
- **Limites de dépense chez le fournisseur** dès que c'est possible (Brave : limite d'usage dans le tableau de bord). Elles s'ajoutent aux
  plafonds de l'app (`LOCAL_*_DAILY_BUDGET`, `LOCAL_PAID_MONTHLY_BUDGET`), elles ne les remplacent pas.
- **Pas de moyen de paiement si ce n'est pas nécessaire** : Serper et Tavily démarrent sans carte.
- Les journaux du worker masquent automatiquement le jeton Telegram et le mot de passe de la base ; **les clés Brave / Tavily / Serper ne sont
  pas masquées** par ce mécanisme : ne colle jamais un extrait de log brut sans le relire.

---

## 4. Où coller une valeur dans Dokploy (procédure commune)

L'app est déployée dans Dokploy comme **application Compose**. Les variables sont communes aux services `web` et `worker` (Dokploy écrit un
fichier `.env` que les deux lisent).

1. Ouvre Dokploy → ton projet → l'application Compose **prospection**.
2. Onglet **Environment**.
3. Ajoute une ligne par variable, au format `NOM=valeur`, **sans espace autour du `=` et sans guillemets**. Exemple :
   ```
   LOCAL_SERPER_API_KEY=colle_ta_cle_ici
   ```
4. **Save**, puis **Deploy** (redéployer). Les variables ne sont lues qu'à la création des conteneurs : un simple redémarrage ne suffit pas.

> **Le préfixe `LOCAL_` est obligatoire pour les clés de recherche.** Les tâches du worker tournent par cron et `worker/entrypoint.sh` ne leur
> transmet que : `DATABASE_URL`, `SEARXNG_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `APP_URL`, `TZ`, `LOG_LEVEL`, `MIGRATIONS_DIR`,
> `PAGESPEED_API_KEY` et toutes les variables commençant par `LOCAL_`. Une variable nommée `SERPER_API_KEY` ou `BRAVE_API_KEY` serait
> **ignorée sans aucun message**.

**Où taper les commandes de test** : Dokploy → conteneur **worker** → **Terminal**, puis commence par `cd /app`. Depuis le VPS, dans le dossier
du projet, tu peux aussi préfixer chaque commande par `docker compose exec worker`.

---

## 5. Les accès de base (obligatoires)

### 5.1 Accès à l'app web — `APP_USER` / `APP_PASSWORD`

- **Utilité** : toutes les pages et API sont protégées par une authentification HTTP Basic (le navigateur affiche une fenêtre « identifiant /
  mot de passe »). Si l'une des deux variables manque, **personne** ne peut entrer (sécurité par défaut).
- **Coût** : gratuit, aucun compte à créer.
- **Étapes** :
  1. Choisis un identifiant (ex. ton prénom).
  2. Génère un mot de passe long (20 caractères ou plus) avec ton gestionnaire de mots de passe, et enregistre-le dedans.
  3. Dans Dokploy → Environment : `APP_USER=...` et `APP_PASSWORD=...`, puis Save et Deploy.
- **Tester** : ouvre `https://ton-domaine` dans une fenêtre de navigation privée. La fenêtre de connexion doit apparaître ; un mauvais mot de
  passe doit être refusé.
- (Il existe `DEV_NO_AUTH`, réservé à l'aperçu local en développement : **ne jamais le mettre dans Dokploy**, il est sans effet en production
  de toute façon.)

### 5.2 Base de données MySQL

- **Utilité** : tout est stocké dans le conteneur `mysql` du même projet. Aucun service extérieur, aucun compte.
- **Variables** : `MYSQL_ROOT_PASSWORD`, `MYSQL_DATABASE` (ex. `prospection`), `MYSQL_USER` (ex. `prospection`), `MYSQL_PASSWORD`, et
  `DATABASE_URL` qui répète l'utilisateur et le mot de passe :
  ```
  DATABASE_URL=mysql://prospection:LE_MEME_MOT_DE_PASSE@mysql:3306/prospection
  ```
- **Étapes** :
  1. Génère deux mots de passe longs (root et utilisateur). Évite les caractères `@ : / # ?` dans le mot de passe utilisateur : ils
     casseraient `DATABASE_URL`.
  2. Colle les 5 variables dans Dokploy → Environment, Save, Deploy.
- **Attention** : les mots de passe MySQL ne sont appliqués qu'au **tout premier démarrage** (volume vide). Les changer ensuite dans Dokploy ne
  change pas le mot de passe dans la base : il faut le changer aussi dans MySQL.
- **Tester** : `cd /app && python -m worker.healthcheck` → la première ligne doit être `✅ base de données : connexion OK`.

### 5.3 SearXNG — `SEARXNG_URL` / `SEARXNG_SECRET`

- **Utilité** : moteur de recherche auto-hébergé, palier 0 (gratuit) de la recherche du site officiel.
- **Variables** : `SEARXNG_URL=http://searxng:8080` (ne pas changer) et `SEARXNG_SECRET` (une chaîne aléatoire).
- **Étapes** : génère une chaîne aléatoire, par exemple sur ton ordinateur avec `openssl rand -hex 32`, et colle-la dans `SEARXNG_SECRET`.
- **Tester** : `cd /app && python -m worker.local.engines` → la ligne « moteurs « general » actifs » doit lister des moteurs.

### 5.4 Domaine — `DOMAIN` / `APP_URL`

- `DOMAIN=prospection.ton-domaine.fr` ; attribue aussi ce domaine dans Dokploy → onglet **Domains** → service `web`, port `3000`.
- `APP_URL=https://prospection.ton-domaine.fr` (facultatif) : les alertes Telegram contiennent alors un lien direct vers la fiche du prospect.

---

## 6. Telegram — `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`

- **Utilité** : alertes des prospects « À contacter » avec boutons de classement (⭐ 📞 🚫 ❌, relevés toutes les 5 minutes), résumé
  quotidien, statistiques hebdomadaires, alertes de dégradation (moteurs bloqués, sources en panne).
- **Obligatoire ?** Non, mais sans lui tu ne reçois aucune alerte.
- **Coût** : gratuit (Bot API gratuite ; limite d'environ 1 message par seconde et par discussion, largement suffisant). Vérifié le 02/10/2026.
- **Lien** : https://t.me/BotFather (dans l'application Telegram).
- **Étapes** (version courte ; le pas à pas complet est dans [`TELEGRAM.md`](../TELEGRAM.md)) :
  1. Dans Telegram, ouvre **@BotFather**, envoie `/newbot`, donne un nom puis un identifiant finissant par `bot`.
  2. BotFather répond avec un **jeton** du type `123456789:AAE...` : c'est `TELEGRAM_BOT_TOKEN`. Copie-le directement dans Dokploy.
  3. Crée un **canal privé**, ajoute ton bot comme **administrateur** (droit « Publier des messages »).
  4. Publie un message dans le canal, transfère-le à **@userinfobot** ou **@getidsbot** : l'identifiant affiché (commence par `-100`) est
     `TELEGRAM_CHAT_ID`.
  5. Dokploy → Environment : `TELEGRAM_BOT_TOKEN=...` et `TELEGRAM_CHAT_ID=...`, Save, Deploy.
- **Tester** : `cd /app && python -m worker.telegram_check` (vérifie le jeton, le chat et l'envoi, et affiche l'erreur exacte de Telegram).
- **Plafond de dépense** : sans objet (gratuit). Si le jeton fuit : `/revoke` auprès de @BotFather, puis remplace-le dans Dokploy.

---

## 7. Moteurs de recherche à clé (facultatifs)

Rappel du fonctionnement : le worker cherche d'abord avec SearXNG (gratuit). Brave et Tavily (palier 1) ne servent que si **aucun** moteur
SearXNG n'a pu répondre ; Serper (palier 2, payant) seulement si les paliers 0 et 1 n'ont pas pu répondre. **Sans clé, le palier est ignoré :
aucune dépense possible.** Un budget quotidien à `0` désactive le fournisseur même si la clé est présente.

**Test commun** (après Save + Deploy) :
```bash
cd /app && python -m worker.local.engines
```
Pour chaque fournisseur tu dois lire `ACTIF`. `inactif (LOCAL_..._API_KEY absente)` = nom de variable faux (préfixe `LOCAL_` ?) ou pas redéployé.
Cette commande **n'envoie aucune requête** aux API payantes : elle montre seulement que la clé est vue. Pour vérifier que la clé est
**acceptée**, utilise le test propre à chaque fournisseur ci-dessous (il consomme **1 requête**). Les erreurs réelles apparaissent aussi dans
`/app/logs/local.log` (« clé refusée (HTTP 401) — vérifie LOCAL_... » ou « quota ou crédits épuisés »).

### 7.1 Serper (palier 2, PAYANT) — `LOCAL_SERPER_API_KEY`

- **Utilité** : résultats Google en France, en tout dernier recours quand les moteurs gratuits sont bloqués.
- **Coût (vérifié le 02/10/2026)** : **2 500 requêtes offertes** à l'inscription, **sans carte bancaire**. Ensuite, packs **prépayés** : le plus
  petit est **50 $ pour 50 000 requêtes (≈ 1 $ / 1 000)**, valables **6 mois** ; les gros packs descendent vers 0,30 $ / 1 000. L'app demande
  10 résultats par requête = 1 crédit. Durée de validité des 2 500 requêtes offertes : **à confirmer**.
- **Lien** : https://serper.dev (bouton **Sign up**).
- **Étapes** :
  1. Va sur https://serper.dev, clique **Sign up**, crée le compte (e-mail ou Google) et confirme l'e-mail.
  2. Une fois connecté, ouvre la page **API Key** du tableau de bord (menu de gauche ; intitulé exact **à confirmer**).
  3. Copie la clé (bouton de copie) et colle-la directement dans Dokploy → Environment :
     ```
     LOCAL_SERPER_API_KEY=ta_cle
     LOCAL_SERPER_DAILY_BUDGET=150
     LOCAL_PAID_MONTHLY_BUDGET=2000
     LOCAL_PAID_ON_EMPTY=0
     ```
  4. Save, Deploy.
- **Tester que la clé est acceptée** (1 crédit) :
  ```bash
  cd /app && python -c "import os,httpx; r=httpx.post('https://google.serper.dev/search', json={'q':'boulangerie Troyes','gl':'fr'}, headers={'X-API-KEY': os.environ['LOCAL_SERPER_API_KEY']}); print(r.status_code)"
  ```
  `200` = OK ; `401` / `403` = clé refusée ; `402` / `429` = crédits épuisés.
- **Plafond conseillé** : `LOCAL_PAID_MONTHLY_BUDGET=2000` (≈ 2 $ / mois) et `LOCAL_SERPER_DAILY_BUDGET=150`. Par défaut,
  `LOCAL_PAID_MONTHLY_BUDGET=0` signifie **aucun plafond mensuel** (seul le plafond quotidien s'applique). Côté Serper, le prépayé est en soi une
  limite : sans pack acheté, rien ne peut être facturé. N'achète pas de pack tant que les crédits offerts ne sont pas épuisés.

### 7.2 Brave Search API (palier 1) — `LOCAL_BRAVE_API_KEY`

- **Utilité** : secours quand aucun moteur SearXNG ne répond.
- **Coût (vérifié le 02/10/2026)** : depuis février 2026, plus d'offre gratuite illimitée. Offre **Search** : **5 $ pour 1 000 requêtes**, avec
  **5 $ de crédit offert chaque mois** (≈ 1 000 requêtes gratuites / mois). **Carte bancaire obligatoire** (anti-fraude, non débitée tant que tu
  restes dans le crédit offert, selon Brave).
- **Lien** : https://api-dashboard.search.brave.com/register
- **Étapes** :
  1. Ouvre le lien ci-dessus, crée le compte, confirme l'e-mail.
  2. Abonne-toi à l'offre **Search** (pas « Answers ») et enregistre la carte.
  3. **Tout de suite** : dans le tableau de bord, règle une **limite d'usage** (usage limit) à 5 $ par mois, pour ne jamais dépasser le crédit
     offert (emplacement exact du réglage : **à confirmer** dans le tableau de bord).
  4. Menu **API Keys** → **Add API Key** → copie la clé, colle-la dans Dokploy :
     ```
     LOCAL_BRAVE_API_KEY=ta_cle
     LOCAL_BRAVE_DAILY_BUDGET=30
     ```
  5. Save, Deploy.
- **Tester que la clé est acceptée** (1 requête) :
  ```bash
  cd /app && python -c "import os,httpx; r=httpx.get('https://api.search.brave.com/res/v1/web/search', params={'q':'boulangerie Troyes'}, headers={'X-Subscription-Token': os.environ['LOCAL_BRAVE_API_KEY'], 'Accept':'application/json'}); print(r.status_code)"
  ```
  `200` = OK ; `401` / `403` / `422` = clé refusée ; `429` = quota.
- **Plafond conseillé** : `LOCAL_BRAVE_DAILY_BUDGET=30` (≈ 930 requêtes sur un mois de 31 jours, sous les ≈ 1 000 offertes) **et** la limite
  d'usage à 5 $ chez Brave. Ne monte pas le budget quotidien au-dessus de 32.

### 7.3 Tavily (palier 1) — `LOCAL_TAVILY_API_KEY`

- **Utilité** : second secours gratuit, au même niveau que Brave.
- **Coût (vérifié le 02/10/2026)** : offre **Researcher** gratuite, **1 000 crédits par mois**, **sans carte bancaire**, remise à zéro le 1er de
  chaque mois. L'app fait des recherches « basic » = **1 crédit** par requête. Au-delà : paiement à l'usage (0,008 $ / crédit) seulement si tu
  l'actives ; sinon les requêtes s'arrêtent jusqu'au mois suivant.
- **Lien** : https://app.tavily.com (inscription et tableau de bord).
- **Étapes** :
  1. Ouvre https://app.tavily.com, crée le compte (e-mail, Google ou GitHub).
  2. Sur la page d'accueil du tableau de bord, la clé (commence par `tvly-`) est affichée : copie-la.
  3. Dans Dokploy :
     ```
     LOCAL_TAVILY_API_KEY=ta_cle
     LOCAL_TAVILY_DAILY_BUDGET=30
     LOCAL_TAVILY_MONTHLY_BUDGET=1000
     ```
  4. Save, Deploy.
- **Tester que la clé est acceptée** (1 crédit) :
  ```bash
  cd /app && python -c "import os,httpx; r=httpx.post('https://api.tavily.com/search', json={'query':'boulangerie Troyes','max_results':1}, headers={'Authorization':'Bearer '+os.environ['LOCAL_TAVILY_API_KEY']}); print(r.status_code)"
  ```
  `200` = OK ; `401` = clé refusée ; `432` / `433` / `429` = quota.
- **Plafond conseillé** : rester sur l'offre gratuite **sans activer le paiement à l'usage** ; `LOCAL_TAVILY_MONTHLY_BUDGET=1000`. Existence d'un
  plafond de dépense dans le tableau de bord Tavily : **à confirmer** (inutile si tu n'ajoutes pas de moyen de paiement).

---

## 8. Sources publiques sans clé (rien à créer)

### 8.1 API Recherche d'entreprises (SIRENE) — aucune variable

- **Utilité** : liste les entreprises actives et diffusables autour de la ville d'une campagne (`recherche-entreprises.api.gouv.fr`).
- **Coût / accès (vérifié le 02/10/2026)** : gratuite, **sans clé et sans compte** ; limite officielle de 7 appels par seconde (l'app se limite
  à environ 3 par seconde).
- **À faire** : rien. Le code n'utilise **pas** l'API Sirene de l'INSEE (portail-api.insee.fr) : **ne crée pas de compte INSEE**, aucune
  variable n'est prévue pour.
- **Tester** : lance une petite campagne dans l'app ; `cd /app && python -m worker.healthcheck` résume l'état des campagnes et du dernier passage.

### 8.2 Export SIRENE en masse (data.gouv.fr) — aucune variable

- **Utilité** : importer un département entier d'un coup au lieu de milliers d'appels.
- **Accès (vérifié le 02/10/2026)** : gratuit, sans compte, mis à jour chaque mois :
  https://www.data.gouv.fr/datasets/base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret/
- **Ce que le code attend** : le fichier **CSV** `StockEtablissement_utf8.csv` (colonnes officielles de l'export). Le format **Parquet**, aussi
  proposé, **n'est pas lu** par l'app. data.gouv.fr annonce l'arrêt des fichiers stock au format CSV courant du 2e semestre 2027 : il faudra
  alors adapter le code.
- **Étapes** :
  1. Sur la page ci-dessus, télécharge **StockEtablissement_utf8** au format **ZIP** (≈ 1,2 Go), décompresse-le (le CSV fait plusieurs Go).
  2. Crée la campagne dans l'app (avec un département ou un code postal) et note son numéro N.
  3. Sur le VPS, dans le dossier du projet : `docker compose cp StockEtablissement_utf8.csv worker:/app/logs/`
  4. Lance : `docker compose exec worker python -m worker.local.bulk /app/logs/StockEtablissement_utf8.csv --campaign N --limit 5000`
  5. La commande affiche des compteurs (`read`, `imported`, `new`…). Supprime ensuite le fichier :
     `docker compose exec worker rm /app/logs/StockEtablissement_utf8.csv`

### 8.3 OpenStreetMap / Overpass — `LOCAL_OSM`, `LOCAL_OSM_URL`

- **Utilité** : complément (site, téléphone, e-mail publics), une requête par campagne, résultat gardé en cache 7 jours.
- **Coût / accès (vérifié le 02/10/2026)** : serveur public `https://overpass-api.de/api/interpreter`, gratuit, sans clé ; usage raisonnable
  demandé (de l'ordre de 10 000 requêtes et 1 Go par jour au maximum), largement au-dessus de ce que fait l'app.
- **Variables** : `LOCAL_OSM=1` (défaut ; `0` pour désactiver) ; `LOCAL_OSM_URL` vide = serveur public, sinon l'adresse de ta propre instance
  Overpass (`https://ton-instance/api/interpreter`). Il n'y a rien à créer pour utiliser le serveur public.

### 8.4 geo.api.gouv.fr et BODACC — aucune variable

Géocodage des villes et centre des communes (geo.api.gouv.fr) ; veille des nouvelles entreprises (BODACC sur OpenDataSoft). Gratuits, sans clé,
utilisés automatiquement.

---

## 9. Ce qu'il ne faut PAS créer

- **PageSpeed Insights (`PAGESPEED_API_KEY`)** : la fonction existe encore dans le code (`worker/worker/local/pagespeed.py`) et la variable est
  transmise au cron, mais **aucune partie de l'app ne l'appelle** depuis que les mesures Lighthouse ont été retirées. Une clé ne servirait à
  rien ; si elle est déjà dans Dokploy, tu peux la supprimer (avec `LOCAL_PAGESPEED_PER_RUN` et `LOCAL_DEEP_PER_RUN`, eux aussi abandonnés).
  Pour mémoire, si la mesure revenait un jour : clé gratuite à créer sur https://console.cloud.google.com/apis/credentials (projet Google Cloud,
  API « PageSpeed Insights » activée, puis « Créer des identifiants → Clé API », restreinte à cette API).
- **SMTP / e-mail** : l'app n'envoie aucun e-mail (jamais de contact automatique des prospects) ; il n'existe aucune variable SMTP.
- **Compte INSEE / API Sirene** : non utilisé (voir 8.1).
- `TELEGRAM_API_BASE`, `TEST_DATABASE_URL`, `DEV_NO_AUTH`, `ALERT_STATE`, `TELEGRAM_OFFSET_STATE`, `LOCAL_LOCK`, `MIGRATIONS_DIR`, `USER_AGENT` :
  réservés aux tests ou réglages internes, **à ne pas mettre** dans Dokploy.

---

## 10. Checklist

**Indispensable (avant le premier déploiement)**
- [ ] `APP_USER` et `APP_PASSWORD` (mot de passe long, rangé dans le gestionnaire de mots de passe)
- [ ] `MYSQL_ROOT_PASSWORD`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD` et `DATABASE_URL` cohérents
- [ ] `SEARXNG_URL=http://searxng:8080` et `SEARXNG_SECRET` aléatoire
- [ ] `DOMAIN` renseigné et domaine attribué dans Dokploy (service `web`, port 3000) ; `APP_URL` si tu veux les liens dans Telegram
- [ ] Save + Deploy, puis `python -m worker.healthcheck` → base de données OK
- [ ] Connexion à l'app testée en navigation privée

**Alertes**
- [ ] Bot créé chez @BotFather, ajouté comme administrateur du canal privé
- [ ] `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans Dokploy, redéployé
- [ ] `python -m worker.telegram_check` passe

**Recherche à clé (facultatif)**
- [ ] Serper : compte créé (sans carte), `LOCAL_SERPER_API_KEY` collée, test → `200`
- [ ] `LOCAL_PAID_MONTHLY_BUDGET=2000` (ou moins) et `LOCAL_SERPER_DAILY_BUDGET=150`
- [ ] Tavily : compte gratuit, `LOCAL_TAVILY_API_KEY` collée, paiement à l'usage **non** activé, test → `200`
- [ ] Brave : compte créé, **limite d'usage de 5 $ réglée chez Brave**, `LOCAL_BRAVE_API_KEY` collée, `LOCAL_BRAVE_DAILY_BUDGET=30`, test → `200`
- [ ] Toutes les clés de recherche commencent par `LOCAL_`
- [ ] `python -m worker.local.engines` → chaque fournisseur voulu est `ACTIF`

**Sécurité**
- [ ] Aucune clé dans Git, dans un chat ou sur une capture d'écran
- [ ] `PAGESPEED_API_KEY`, `LOCAL_PAGESPEED_PER_RUN`, `LOCAL_DEEP_PER_RUN` retirées de Dokploy si présentes
- [ ] Rappel noté : revérifier la consommation Serper (`month_requests`, voir `GUIDE_CONNEXION.md` section 8) une semaine après l'activation
