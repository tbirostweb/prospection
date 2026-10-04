# 🎯 Prospection locale

Application **self-hosted** qui trouve, autour d'une ville, les commerces, artisans et TPE qui pourraient avoir intérêt à un nouveau site (refonte, SEO,
HTTPS, mobile, performance), et te les présente **classés, expliqués et avec leur niveau de confiance**. Aucune IA : découverte par données ouvertes,
recherche du site officiel vérifiée par preuves, contacts publics, score par règles explicables (audit passif du site en option : `LOCAL_AUDIT=1`).

**Ce sont des prospects à froid** : ils n'ont rien demandé. L'app ne dit jamais qu'ils « cherchent un développeur » et n'envoie **jamais** de message à leur place.
**Coût :** VPS + nom de domaine. Par défaut, aucune API payante ; un moteur payant (Serper) peut être ajouté en dernier recours, avec un budget plafonné (voir [docs/GUIDE_CONNEXION.md](docs/GUIDE_CONNEXION.md)).

---

## Ce que tu obtiens

- Un **dashboard privé** : campagnes (ville + rayon + activités, ou **pré-recherches** prêtes à l'emploi : « Beauté & bien-être », « Artisans du bâtiment »… et les tiennes), liste de prospects filtrable et triable, fiche expliquée, file « À contacter », statistiques et **santé du système**.
- Une **carte interactive** : zone et rayon au choix (clic ou point déplaçable), pastilles de couleur « où j'en suis » (à contacter, contacté, réponse, intéressé, gagné, perdu…), filtres, actions rapides, campagne lancée depuis un point.
- Un **pipeline commercial** (Kanban, glisser-déposer) avec taux de conversion et « à relancer » après 7 jours sans réponse.
- Des **brouillons d'e-mail personnalisés** créés à la demande (bouton « Créer un brouillon »), à partir des faits détectés, signés avec ton identité — jamais envoyés par l'application.
- Une **veille des nouvelles entreprises** (BODACC + SIRENE) par campagne, chaque semaine ou chaque mois, annoncée sur Telegram.
- Des **alertes Telegram** pour chaque prospect « À contacter » ou mieux, avec des boutons pour le **classer depuis ton téléphone** : ⭐ bon prospect · 📞 contacté · 🚫 pas intéressé (+ la raison) · ❌ mauvais site.
- Un **résumé du matin** (07:15 : relances du jour, 3 meilleurs prospects, tournée proposée avec l'itinéraire) et un **bilan hebdomadaire** (lundi 08:00) sur Telegram, et des **alertes techniques** en cas de dégradation.
- Une **recherche par paliers**, du gratuit au payant : moteurs SearXNG d'abord ; puis, seulement si aucun n'a pu répondre, les API à quota gratuit **Brave Search** (`LOCAL_BRAVE_API_KEY`, 30 requêtes / jour par défaut) et **Tavily** (`LOCAL_TAVILY_API_KEY`, 30 / jour et 1 000 / mois) ; enfin, en dernier recours, **Serper** (payant, `LOCAL_SERPER_API_KEY`, 150 / jour par défaut, plafond mensuel `LOCAL_PAID_MONTHLY_BUDGET`). Sans clé, rien de payant n'est jamais appelé. Pas à pas : [docs/GUIDE_CONNEXION.md](docs/GUIDE_CONNEXION.md).

---

## Architecture

```
Campagne (ville + rayon + activités)
  → SIRENE (entreprises actives) → exclusions / chaînes → OSM (complément)
  → recherche du site officiel (SearXNG, puis API Brave / Tavily, puis Serper payant ; preuves pondérées, vérification inversée)
  → [audit passif si LOCAL_AUDIT=1] → contacts publics
  → score explicable → alerte Telegram → classement (toi)
```

| Service | Rôle | Exposé ? |
|---|---|---|
| **web** | Next.js — dashboard + API | Oui (via Traefik/Dokploy) |
| **worker** | Python (cron) — campagnes, scoring, Telegram | Non |
| **mysql** | Base de données | Non (réseau privé) |
| **searxng** | Méta-moteur de recherche (site officiel des entreprises) | Non |

Seul `web` est accessible depuis Internet ; tout le reste vit sur un réseau interne.

---

## Prérequis

- Un **VPS** avec Docker (**2 à 4 Go de RAM suffisent** : plus d'IA locale).
- **Dokploy** installé sur le VPS (fournit Traefik + HTTPS Let's Encrypt + backups).
- Un **nom de domaine** dont le **A record** pointe vers l'IP du VPS.
- Un **bot Telegram** (voir [TELEGRAM.md](TELEGRAM.md)).

---

## Déploiement sur Dokploy — étape par étape

### 1. Pousser le code sur un dépôt Git

```bash
git add -A && git commit -m "Prospection"
git push   # GitHub / GitLab (privé de préférence)
```

### 2. Créer le service dans Dokploy

Dokploy → **Create Service → Compose** → connecte ton dépôt.
Chemin du fichier compose : **`docker-compose.yml`** (à la racine).

### 3. Renseigner les variables d'environnement

Onglet **Environment** du service (Dokploy écrit un `.env`). Reprends
[.env.example](.env.example) en remplaçant les valeurs :

| Variable | Rôle | Exemple |
|---|---|---|
| `DOMAIN` | Ton domaine | `prospection.mondomaine.fr` |
| `APP_USER` | **Login d'accès à l'app** | `theo` |
| `APP_PASSWORD` | **Mot de passe d'accès** (14 caractères minimum, sinon accès refusé) | `xK9…` |
| `MYSQL_ROOT_PASSWORD` | Mot de passe root MySQL | `…` |
| `MYSQL_DATABASE` | Nom de la base | `prospection` |
| `MYSQL_USER` / `MYSQL_PASSWORD` | Compte applicatif MySQL | `prospection` / `…` |
| `DATABASE_URL` | URL DB (host = `mysql`) | `mysql://prospection:…@mysql:3306/prospection` |
| `APP_URL` | URL publique (facultatif : lien dans les alertes Telegram) | `https://prospection.mondomaine.fr` |
| `SEARXNG_SECRET` | Secret SearXNG | `…` |
| `TELEGRAM_BOT_TOKEN` | Token du bot | `123…:AAE…` |
| `TELEGRAM_CHAT_ID` | ID de ton canal | `-1001234567890` |

> ⚠️ `APP_USER` **et** `APP_PASSWORD` sont **obligatoires** : sans eux, l'app
> refuse tout accès (renvoie 401). C'est ta seule barrière d'entrée.
> Vérifie que `DATABASE_URL` contient bien le **même mot de passe** que `MYSQL_PASSWORD`.

### 4. Déployer

Clique **Deploy**. Au premier lancement :
- MySQL crée le schéma ; le worker joue les migrations et recalcule les scores.
- Le worker reprend les campagnes en attente **toutes les 15 min de 6h à 22h** ; les boutons Telegram sont relevés toutes les 5 min.

Suivre les logs :
```bash
docker compose logs -f worker
```

### 5. Domaine + HTTPS

Onglet **Domains** → service **`web`**, port **`3000`**, ton domaine, active le
**certificat Let's Encrypt**. Vérifie d'abord que le DNS est propagé :
```bash
dig +short prospection.mondomaine.fr    # doit renvoyer l'IP du VPS
```

### 6. Se connecter

Ouvre `https://ton-domaine` → le navigateur demande **login / mot de passe**
(ceux de `APP_USER` / `APP_PASSWORD`). C'est bon, l'app est à toi.

### 7. Configurer Telegram

Suis **[TELEGRAM.md](TELEGRAM.md)** (bot + canal privé), renseigne
`TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`, redéploie, puis teste depuis le
terminal du conteneur `worker` (diagnostic complet + message de test) :
```bash
cd /app && python -m worker.telegram_check
```

---

## Première utilisation (dans l'app)

1. **Paramètres** — renseigne ta ville de référence et le rayon par défaut ; ajuste si besoin les poids du score, les seuils de fiabilité du site et le poids de chaque activité.
2. **Prospection locale → Nouvelle campagne** (ou **Carte → Lancer une campagne ici**) : zone, rayon, une pré-recherche ou des activités, nombre maximum, et si tu veux la **veille** des nouvelles entreprises. Le worker la traite au passage suivant (≤ 15 min).
   **Paramètres → Mon identité** : ton nom et tes coordonnées pour signer les brouillons.
3. Suis-la dans la liste (« Mesures de la campagne », entonnoir, couverture) ; ouvre une fiche pour voir **pourquoi** un prospect est classé ainsi.
4. Classe depuis Telegram ou l'app : ⭐ 📞 🚫 (+ raison) ❌ **Mauvais site** ; **J'ai trouvé le site** si la recherche a raté un site connu.

---

## Comment lire un résultat

- **Site** : CONFIRMED (≥ 0,90) · PROBABLE (≥ 0,80) · UNCERTAIN (≥ 0,60, jamais audité) · NOT_FOUND · UNREACHABLE. « Site non trouvé » **ne prouve pas** l'absence de site : la fiche affiche la couverture de recherche (ex. 6/8 stratégies) et une confiance d'absence toujours < 0,85.
- **Deux scores** : *potentiel commercial* (l'intérêt apparent) et *fiabilité des données* (la qualité de l'information). Chaque plafond est expliqué (« Score brut 86 → plafond 74 : raison »).
- **🔥 Très bon** exige : fiabilité ≥ 70, besoin observable (jamais « site non trouvé » seul), contact exploitable, activité pertinente. **🟢 À contacter** exige un contact professionnel ou un canal officiel. Chaînes, réseaux et franchises sont BANNIS dès la découverte (jamais importés ni recherchés) : ~700 enseignes par métier dans `worker/worker/local/franchises.py`, plus les tiennes (Paramètres → Enseignes bannies). Ceux repérés plus tard par leur site sont écartés (score 0).
- **Priorité de ciblage** (Paramètres) : « 🛠️ Refonte d'abord » par défaut — les entreprises qui ONT un site daté ou faible passent devant celles sans site ; « Sans site d'abord » ou « Équilibré » au choix. Les sites gratuits Wix / Jimdo (`compte.wixsite.com/mon-site`) sont reconnus comme sites officiels (vérifiés comme les autres) et signalés, comme les sites faits avec un créateur de sites.
- **Fiches prêtes seulement** : « À contacter », la tournée et le résumé du matin ne proposent que des fiches COMPLÈTES (traitement terminé sans erreur, enrichissement fait, site tranché, au moins un contact) ; les autres sont marquées « en cours de complétion ». Une entreprise = une fiche (un 2e établissement du même SIREN n'est pas importé). 60 entreprises par campagne par défaut (jusqu'à 1 000).
- **Qualité plutôt que quantité** : métiers favorisés (⭐ artisans du bâtiment, libéraux, beauté, auto, agences immobilières, paysagistes, photographes, déménageurs, auto-écoles, hébergements, artisans d'art…) ; activités bruitées (restaurants, bars, boulangeries, commerces, boutiques, opticiens, associations) gardées SEULEMENT avec un vrai signal (pas de site après une vraie recherche, site en panne ou à moderniser, ouverture / reprise récente, réseaux sans site). Holdings, SCI, loueurs de biens, syndics, administrations et groupes locaux multi-sites exclus. « Aucun site » n'est jamais affirmé avant une vraie recherche (≈ 2/3 des stratégies, moteurs non dégradés).
- **L'essentiel par entreprise** : a-t-elle un site (officiel, vérifié) ? et un moyen de la contacter. L'ancien enrichissement des prospects qualifiés (recherche élargie, dirigeant, CA), les mesures Lighthouse / PageSpeed, les revérifications de sites et l'interrogation BODACC par SIREN ne sont plus faits par le worker de campagne : le débit va aux fiches nouvelles.
- **Probabilité d'achat** : le score favorise ceux qui ont une raison d'acheter MAINTENANT (🔑 reprise, 🆕 ouverture récente sans site, 🏷️ changement de nom, 📦 déménagement — BODACC, quand l'événement est connu (veille) : le worker de campagne n'interroge plus le BODACC par SIREN —, 💥 site en panne, 🧱 site gratuit ou PagesJaunes…), un budget probable (métier à forte valeur par client, CA publié, effectif) et **ce qui a marché pour toi** : dès 10 résultats marqués (réponse, client, perdu, sans réponse), les métiers, villes et signaux qui répondent gagnent jusqu'à +10 points (Statistiques → « Ce que tes résultats m'apprennent » ; `python -m worker.local.learning`). Ces résultats survivent à « Tout effacer ».
- **Fiche « Pour vendre »** (en tête de chaque prospect, déduite des données déjà connues, rien d'inventé) : résumé en une ligne, pourquoi lui, pourquoi maintenant, argument à utiliser, prochaine action (appeler, e-mail, relancer, passer sur place) et message conseillé court (premier contact ou relance), à copier ou ouvrir dans ta messagerie — jamais envoyé automatiquement.
- **Suivi** : « À contacter » liste les relances dues (J+3, J+10, sans réponse à J+21) ; chaque fiche indique le dirigeant s'il est connu (il n'est plus recherché automatiquement), quand appeler selon le métier et les horaires OSM ; « Tournée » propose les meilleurs prospects dans un ordre de passage court, avec l'itinéraire Google Maps.
- **Contacts** : uniquement publics et professionnels, avec leur source (site officiel, OpenStreetMap, ou **extrait d'un résultat de recherche** citant le nom ET la ville — les annuaires ne sont jamais ouverts, les numéros surtaxés 08 refusés) ; jamais d'adresse devinée.
- **Audit** (désactivé par défaut, `LOCAL_AUDIT=1` pour l'activer) : passif et léger (jamais de scan de sécurité) ; « aucun problème majeur détecté », « bases SEO manquantes », « SEO technique améliorable » — jamais « mauvais référencement » sans donnée réelle. Page rendue en JavaScript ou protégée par anti-bot : SEO « non évaluable ».

Règles détaillées : `.claude/skills/local-prospecting/SKILL.md` · sources et mesures réelles : [docs/SOURCES.md](docs/SOURCES.md).

---

## Exploitation

- **Logs** : `docker compose logs -f worker` (ou `/app/logs/*.log`). Rotation auto quotidienne.
- **Santé** : page **Statistiques → Santé du système** (moteurs, sources, erreurs, dégradations, entonnoir) ; en ligne de commande, dans le terminal du conteneur `worker` :
  ```bash
  cd /app
  python -m worker.audit_system        # rapport complet, lecture seule
  python -m worker.local.engines       # paliers, budgets, consommation du jour / du mois, moteurs SearXNG actifs et ce que rend chacun
  python -m worker.healthcheck         # base, SearXNG, moteurs, campagnes
  ```
- **Alertes** : Telegram quand un problème APPARAÎT (moteurs tous bloqués, taux de sites trouvés qui s'effondre, source indisponible…), une seule fois tant qu'il dure. Un budget de requêtes du jour atteint n'est pas une panne : simple avertissement.
- **Veille** : `python -m worker.local.watch` (cron quotidien 07:10) — chaque campagne où la veille est activée, à son rythme.
- **Mesures** : `python -m worker.local.evaluate cas.json` (précision, rappel, faux sites), `python -m worker.local.calibration` (score vs tes décisions), `python -m worker.local.golden export` (tes retours → cas de test).
- **Validation de production** : `sh scripts/vps_check.sh` sur le VPS (builds, réseau Docker, SearXNG depuis l'IP du VPS, MySQL, cron, ressources).
- **Repartir de zéro** (terminal du conteneur `worker`). D'abord l'**aperçu**, qui ne modifie rien :
  ```bash
  cd /app && python -m worker.reset
  ```
  Puis, si le compte te convient : `python -m worker.reset --yes [--telegram] [--keep-local]`. Supprime les prospects locaux, leurs caches et l'état technique.
  **Conserve toujours** : la liste « ne plus contacter », les « mauvais sites », tes retours, tes réglages et la configuration de tes campagnes.
- **Nettoyage de l'ancien pipeline (facultatif, irréversible)** : les anciennes tables d'opportunités (annonces, sources Codeur/BOAMP…) ne sont plus utilisées mais **n'ont pas été supprimées**. Après une sauvegarde : `docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < db/cleanup_legacy.sql`.
- **Sauvegarde DB** (en plus des backups Dokploy) :
  ```bash
  docker compose exec mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' | gzip > backup.sql.gz
  ```
  👉 **Teste une restauration au moins une fois.**

---

## Dépannage

| Symptôme | Cause probable / solution |
|---|---|
| Page 401 en boucle | `APP_USER`/`APP_PASSWORD` non définis ou mauvais identifiants. |
| Campagne « en attente » | Moteurs de recherche en cooldown ou budget du jour atteint : normal, elle reprend seule (`python -m worker.audit_system` donne la cause). Pour plus de débit : clés Brave / Tavily / Serper ([docs/GUIDE_CONNEXION.md](docs/GUIDE_CONNEXION.md)). |
| Beaucoup de « site non trouvé » | Vérifie `python -m worker.local.engines` : seuls les moteurs web réellement actifs de ton SearXNG comptent. |
| Pas de notif Telegram | Token/chat_id manquants, ou bot pas admin du canal : `python -m worker.telegram_check`. |
| « Base non initialisée » | MySQL pas prêt : attends le healthcheck, vérifie `DATABASE_URL`. |
| Grande zone (un département entier) | Télécharger l'export officiel `StockEtablissement_utf8.csv` (data.gouv.fr), puis `python -m worker.local.bulk fichier.csv --campaign N` : lu en flux, filtré (actifs, diffusables, département, activités), chaînes bannies, dédoublonné par SIRET, situé au centre de sa commune. |
| Conservation des données | `python -m worker.local.retention` (chaque nuit, 04:30) : prospect jamais travaillé supprimé après 18 mois (`LOCAL_RETENTION_MONTHS`), exclu après 6 mois ; « ne plus contacter », tes retours et tes résultats jamais supprimés. |
| Vérifier l'interface sans données réelles | `python scripts/demo_db.py` (base `prospection_demo` sur le MySQL de test), puis la configuration « web-demo » de `.claude/launch.json` : `next dev` sur 127.0.0.1:3010 sans mot de passe (`DEV_NO_AUTH=1`, refusé hors développement et hors localhost). |
| Résumé à la mauvaise heure | Le conteneur worker est en `TZ=Europe/Paris` (déjà configuré). |
| Certificat HTTPS KO | A record pas propagé au moment de l'émission — refais un déploiement. |

---

## Développement local (tests)

Tests unitaires (aucune base requise) et d'intégration (**vrai MySQL**, réseau et Telegram simulés) :
```bash
cd worker && pip install -e ".[dev]" && PYTHONPATH=. pytest -q                      # unitaires seuls
URL=$(scripts/dev_mysql.sh start)                                                   # MySQL jetable en local (requiert mysqld, ex. brew install mysql)
cd worker && TEST_DATABASE_URL=$URL PYTHONPATH=. pytest -q                          # + intégration
scripts/dev_mysql.sh stop
```
L'image de production est **Python 3.11** : un test garde contre les syntaxes réservées à 3.12. À lancer avant de livrer toute migration ou requête SQL.

---

## Mettre à jour la base après coup (migrations)

Le schéma initial (`db/migrations/`) n'est joué qu'à la création de la base. Toute modification ultérieure : un fichier SQL **idempotent** numéroté dans **`db/updates/`**.
Au prochain déploiement, le worker joue automatiquement les fichiers pas encore appliqués (une seule fois chacun ; suivi dans `schema_migrations`).
```bash
python3 scripts/check_migrations.py      # garde-fou (à lancer avant de livrer)
```
