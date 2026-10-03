---
name: prospect-detection
description: Règles de détection des prospects pour l'app Prospection — haute précision, validité avant score, classification structurée, budget honnête, tests de régression golden. À appliquer à tout changement touchant la recherche, la classification, le filtrage, l'extraction de budget, le scoring ou les prompts Ollama.
---

# Prospection — Prospect Detection Skill

## Mission
Prospection recherche des ACHETEURS de prestations web, jamais simplement des pages qui parlent de développement web.
L'objectif est la **haute précision** : mieux vaut 10 vraies opportunités que 500 résultats bruités.

## Question centrale
Pour chaque page candidate : *la personne ou l'organisation derrière ce contenu exprime-t-elle actuellement un vrai besoin
qu'un tiers réalise un projet web ?*
- OUI clairement → on continue. NON → rejet. Incertain → UNKNOWN / à vérifier, **jamais** de notification prioritaire.

## Opportunités valides
Cherche un développeur web / un freelance / une agence web · demande de refonte · nouveau site · projet e-commerce ·
projet WordPress · application web · demande de maintenance · demande de migration · demande de devis · appel d'offres
public · consultation · déclaration explicite qu'un prestataire est nécessaire.

## Invalides
- **Vend le service** : site d'agence, portfolio de freelance ou de développeur, page de service, page de tarifs,
  « nous créons des sites », « nos services », « nos réalisations ».
- **Informatif** : articles, tutoriels, guides, pages SEO, modèles, thèmes, annuaires, exemples, inspiration.
- **Emploi** : CDI, CDD, stage, alternance, recrutement salarié.
- **Besoin passé / terminé** : projet réalisé, marché attribué, prestataire sélectionné, projet livré, demande expirée.

## Classification obligatoire (chaque candidat)
`pageType`, `isBuyer`, `isCurrentNeed`, `needsWebProvider`, `buyerIntentScore`, `confidence`, `evidence[]`, `rejectReason`.
Types autorisés : CLIENT_NEED · PUBLIC_TENDER · MARKETPLACE_LISTING · SERVICE_PROVIDER · PORTFOLIO · BLOG_ARTICLE ·
DIRECTORY · EMPLOYMENT · OLD_PROJECT · TEMPLATE · UNKNOWN.

## Acceptation
`pageType ∈ {CLIENT_NEED, PUBLIC_TENDER, MARKETPLACE_LISTING}` ET `isBuyer` ET `isCurrentNeed` ET `needsWebProvider` ET
`buyerIntentScore >= 60` ET `confidence >= 0.70`.
**Notification Telegram** : `confidence >= 0.80` ET `finalScore >= seuil configuré`.

## Distinction essentielle
Un site d'entreprise vieux ou mauvais n'est PAS une opportunité actuelle. On sépare `explicitOpportunity` de
`coldProspectingLead` : le dashboard principal ne montre que des opportunités explicites. Ne jamais convertir
silencieusement un prospect « à froid » en opportunité explicite.

## Budget
N'extraire un budget de prospect qu'APRÈS avoir établi l'intention d'achat. Ne jamais lire un prix de prestataire comme un budget
client (« forfaits à partir de 999 € » sur une page d'agence ≠ budget). Toujours conserver : `budgetMin`, `budgetMax`,
`budgetRawText`, `budgetType`, `budgetSource`, `budgetConfidence`. Une estimation de l'IA n'est jamais un budget explicite.

## Politique LLM
Ollama est un vérificateur et un extracteur structuré, PAS l'unique filtre. Le code déterministe passe d'abord. Sortie JSON
structurée, température basse, aucune information inventée, en cas de doute : UNKNOWN / null. L'app fonctionne sans fine-tuning.

## Philosophie de recherche
Éviter les requêtes vendeuses larges (« création site internet »). Préférer des requêtes d'ACHETEUR :
`"recherche prestataire" "site internet"` · `"cherche développeur" "site internet"` · `"nous recherchons" "refonte site"` ·
`"demande de devis" "site internet"` · `"appel d'offres" "site web"` · `"consultation" "refonte site"` ·
`"besoin développeur web"` · `"recherche freelance wordpress"`.

## Scoring
Le score n'intervient qu'APRÈS les contrôles de validité : une page invalide ne se rattrape pas par un bon score.
Le rejet dur précède le scoring. Dimensions recommandées : intention d'achat 30 · budget/valeur 20 · compatibilité projet 15 ·
fraîcheur 15 · adéquation client 10 · technologies 5 · lieu/remote 5.
> Note d'implémentation : les poids en vigueur sont ceux demandés explicitement par Théo (budget 10, type de client hors note,
> compétences et type de projet prépondérants). En cas de conflit, ne pas trancher seul : le signaler.

## Règle de précision
Un faux positif coûte plus cher qu'un faux négatif. Dans le doute, pas de Telegram. Les cas UNKNOWN peuvent être stockés pour revue.

## Tests de régression
Maintenir un jeu golden : vrais prospects, prestataires, freelances, agences, blogs, offres d'emploi, projets historiques,
exemples ambigus. Toute erreur de classification réelle importante devient un test de régression.
**Ne jamais « réparer » un test en changeant la valeur attendue pour améliorer les métriques.**

## Règle de développement
Tout changement touchant la recherche, la classification, le filtrage, l'extraction de budget, le scoring ou les prompts Ollama
doit exécuter les tests de détection de prospects avant d'être considéré comme terminé (`pytest tests/test_prospect_detection.py`).
Ne jamais prétendre à une fiabilité parfaite : rapporter la précision et les erreurs réellement observées par les tests.
