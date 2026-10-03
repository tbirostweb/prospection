# Sources de la prospection locale — ce qui est branché, ce qu'on en sait, ce qui n'est pas mesuré

Règles appliquées à toute source : **gratuite** (seule exception : le palier payant Serper, facultatif, en dernier recours et plafonné), **autorisée** (robots.txt, CGU, licence), **testée en réel** avant d'être branchée, jamais de contournement de
CAPTCHA, de connexion, d'anti-bot ni de limite d'API. Mesures faites le 21/09/2026 depuis un poste de développement (IP résidentielle) et, pour les moteurs,
depuis le terminal du conteneur `worker` du VPS.

| Source | Fonction | État |
|---|---|---|
| **API Recherche d'Entreprises (SIRENE)** | Découverte des entreprises actives et diffusables autour d'un point (`near_point`), dédup SIRET | Testée en réel. Les résultats arrivent classés par taille (chaînes d'abord) : seuls les indépendants comptent pour le plafond. Import bulk (CSV officiel) **testé seulement sur fixture** |
| **geo.api.gouv.fr** | Géocodage ville → coordonnées ; commune homonyme sans code postal refusée avec les alternatives | Testé en réel (jamais Nominatim) |
| **BODACC** (OpenDataSoft) | Veille des créations récentes (signal, pas une preuve de besoin) | Testé en réel. **N'est plus interrogé par SIREN pendant les campagnes** (radiations, reprises, changements de nom ne sont donc plus relevés pour les fiches de campagne) |
| **OpenStreetMap (Overpass)** | Complément : site, téléphone, e-mail publics — une requête par campagne, cache 7 jours, correspondance ≤ 120 m et nom ≥ 75 % | Testé en réel ; réponse plafonnée à 5 000 objets (zone dense = couverture partielle **signalée**). `LOCAL_OSM=0` désactive |
| **SearXNG** (auto-hébergé) — palier 0 | Recherche du site officiel : stratégies progressives, moteurs actifs découverts depuis `/config`, santé / cooldown / budget par moteur | Voir « Moteurs » ci-dessous |
| **API Brave Search**, **API Tavily** — palier 1 | Secours à quota gratuit, seulement si aucun moteur SearXNG n'a pu répondre | Facultatif (clé). Brave branché et testé sur fixture ; Tavily **testé seulement sur fixture** |
| **API Serper** (résultats Google) — palier 2, PAYANT | Dernier recours, budget quotidien et mensuel plafonnés | Facultatif (clé). **Testé seulement sur fixture** |
| **Audit HTML passif** | ≤ 4 GET par site : SEO (preuves), technique, performance légère, indices de modernisation | Testé en réel sur un corpus de 17 URLs. **Désactivé par défaut** (`LOCAL_AUDIT=1` pour l'activer) |
| ~~PageSpeed / Lighthouse~~ | Retiré du worker de campagne (jamais de résultat obtenu en réel) | Plus utilisé |

## Moteurs de recherche (mesures réelles)

- **VPS (terminal du conteneur `worker`)** : `python -m worker.local.engines` a montré que l'instance n'a que **3 moteurs web actifs : brave (20 résultats), google cse (20), duckduckgo (5)**. Les autres moteurs de la catégorie « general » (wikipedia, wikidata, currency, lingva, dictzone) sont des outils : exclus. Demander `engines=mojeek` ne rendait aucun résultat de mojeek (moteur désactivé) : détecté et écarté.
- **Poste de développement** : après ~1 500 requêtes en une heure, brave (429), mojeek et startpage (0 résultat **sans erreur**) se sont bloqués : d'où le rythme par moteur (3 s), le budget de 300 requêtes / moteur / 24 h, la détection du blocage silencieux (8 réponses vides d'affilée + requête témoin) et la mesure de la qualité par la **pertinence** des résultats.
- Un budget de 300 requêtes par moteur et par 24 h (`LOCAL_ENGINE_DAILY_BUDGET`). Le fichier `searxng/settings.yml`, embarqué dans l'image du service `searxng`, active 6 moteurs web (google, bing, qwant, yahoo, brave, duckduckgo), plus « google cse » actif par défaut ; mojeek et startpage sont marqués inactifs par SearXNG (CAPTCHA à preuve de travail) : **à vérifier depuis l'IP du VPS** avec `python -m worker.local.engines`, car un moteur déclaré peut être bloqué ou ne rien rendre.

## Ordre de recherche : du gratuit au payant

1. **Palier 0 — SearXNG** : au plus 2 moteurs qui répondent par requête, le 2e **seulement si le 1er n'a rendu aucun résultat** (avant : 2 moteurs systématiquement).
2. **Palier 1 — API à quota gratuit** (Brave, puis Tavily) : interrogées **seulement si aucun moteur SearXNG n'a pu répondre** (cooldown, budget épuisé, SearXNG en panne). Une seule API qui répond suffit.
3. **Palier 2 — Serper, payant** : seulement si les paliers 0 et 1 n'ont pas pu répondre. Plafonds : `LOCAL_SERPER_DAILY_BUDGET` (150 / 24 h) et `LOCAL_PAID_MONTHLY_BUDGET` (0 = pas de plafond mensuel).

Une réponse **vide** du gratuit ne déclenche pas le payant, sauf `LOCAL_PAID_ON_EMPTY=1`. Le budget n'est jamais dépassé (re-vérifié juste avant chaque appel d'API). Consommation suivie dans `local_engine_health` (fenêtre de 24 h et compteur mensuel, migration 022).

## Débit estimé (estimation, pas une mesure)

Côté résolution du site, les changements récents visent environ **6 requêtes par entreprise, contre environ 12 avant** : jusqu'à 5 domaines devinés d'après le nom (vérifiés par DNS puis lus directement, sans requête aux moteurs), stratégies réordonnées de la plus productive à la moins productive (nom + ville, raison sociale + ville, nom + téléphone, nom + adresse…), requêtes en double supprimées (casse, accents, guillemets), arrêt dès qu'un site est confirmé, et 2e moteur SearXNG seulement sur un vide. L'approfondissement des prospects qualifiés et les revérifications, qui consommaient aussi des requêtes, ont été retirés.

À titre d'ordre de grandeur seulement : avec 300 requêtes par moteur et N moteurs SearXNG réellement actifs, compter environ 300 × N ÷ 6 entreprises par 24 h, plus ce qu'apportent les budgets Brave / Tavily / Serper. **Aucun chiffre n'est garanti** : il dépend du nombre de moteurs qui répondent vraiment depuis l'IP du VPS, des blocages, de la proportion d'entreprises dont le site est trouvé tôt et de `LOCAL_LOOKUPS_PER_RUN` / `LOCAL_TIME_BUDGET_S`. Le nombre réel de requêtes par entreprise est à relever dans `local_run_metrics`.

## Ce qui a été observé sur des données réelles

- **Annuaires** : sur 59 entreprises de Troyes, 30 « sites incertains » étaient des annuaires (actulegales, infonet, infobel…) ; corrigé. Après correction : 5 sites confirmés, 0 faux site constaté.
- **Audit** : une page rendue en JavaScript ou protégée par anti-bot donnait un faux « SEO mauvais » ; désormais « non évaluable ».
- **Python 3.11** : le build Docker a révélé une syntaxe réservée à Python 3.12 qui aurait empêché le pipeline de démarrer ; garde-fou en test.

## Non mesuré (à ne pas déduire de ce document)

Précision et rappel de la résolution de site sur un échantillon représentatif · précision et rappel des contacts · comportement de la recherche sous charge depuis l'IP du VPS ·
nombre réel de requêtes par entreprise après les changements de paliers et de stratégies · API Tavily et Serper en réel · détection des chaînes sur données réelles.
