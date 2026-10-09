---
name: local-prospecting
description: Règles de la prospection LOCALE proactive de l'app Prospection (commerces, artisans, TPE autour d'une ville) — LOCAL_PROSPECT ≠ HOT_OPPORTUNITY, résolution prudente du site officiel, audit passif, score explicable, jamais de contact automatique. À appliquer à tout changement dans worker/worker/local/, web/lib/local.ts ou les pages /local.
---

# Prospection locale — règles

## Périmètre
- L'application ne fait plus QUE de la prospection locale (l'ancien pipeline d'opportunités — Codeur, BOAMP, recherche web, analyse IA — a été supprimé). Un `LOCAL_PROSPECT` est une entreprise découverte par nous, qui n'a RIEN demandé : ne jamais écrire ni laisser entendre qu'elle « cherche un développeur » (formulation : « peut avoir intérêt à… »).
- Telegram : alertes des prospects « À contacter » ou mieux avec boutons de classement (⭐ 📞 🚫 + raison, ❌ Mauvais site) ; « ne plus contacter » n'est jamais modifiable par un bouton. Aucune IA (Ollama supprimé).

## Site officiel
- Ne jamais prendre le premier résultat. Chaque candidat est LU et reçoit une confiance issue d'une accumulation de PREUVES pondérées (`websiteEvidence[]` : SIRET/SIREN très fort, adresse très forte, téléphone fort, nom + ville moyen, nom seul faible — plafonné à 0,55) et d'une VÉRIFICATION INVERSÉE (un autre SIREN valide sur le site = preuve négative forte, interdit d'accepter).
- Niveaux : CONFIRMED ≥ 0,90 · PROBABLE ≥ 0,80 · UNCERTAIN ≥ 0,60 · NOT_FOUND · UNREACHABLE (seuils réglables, `settings.local_options.website`). Un site PROBABLE n'est JAMAIS compté comme confirmé dans les métriques ; un site UNCERTAIN n'est jamais audité.
- Stratégies de recherche PROGRESSIVES (nom+ville, raison sociale, SIREN, code postal, adresse, téléphone, activité…) : on s'arrête dès qu'un site est confirmé. `NOT_FOUND` s'accompagne de la couverture (essayées / applicables) et d'une confiance d'absence plafonnée à 0,85 — jamais une certitude, réduite si la recherche était dégradée.
- Domaine canonicalisé (redirections, www, http) avant toute déduplication ; « Mauvais site » = domaine exclu pour ce SIRET ; « J'ai trouvé le site » = validé comme n'importe quel candidat, jamais accepté sur parole.
- Absence de site trouvé ≠ absence certaine. Afficher « Site non trouvé ». Une recherche en échec (moteurs en cooldown) n'écrit RIEN sur le prospect.
- Un faux site associé est PIRE qu'un site non trouvé : ne jamais baisser un seuil pour « améliorer le rappel » ; mesurer avec `python -m worker.local.evaluate`.

## Recherche multi-moteurs
- Chaque moteur SearXNG a santé, cooldown exponentiel, dernier succès/échec ; un moteur CAPTCHA / 429 / timeout est retiré, les autres continuent ; tous en panne ⇒ `SearchUnavailable` (campagne en attente), jamais une liste vide. Seul le moteur interrogé est incriminé (SearXNG liste aussi des moteurs non demandés).

## Audit
- Passif et léger : ≤ 4 GET via `net.try_get` (SSRF-safe), robots respecté. JAMAIS : scan de ports, vulnérabilités, mots de passe, injections, exploits, contournement de protection.
- SEO, technique, performance, design séparés. Formulations prudentes : « bases SEO manquantes », « SEO technique améliorable », « aucun problème majeur détecté ». Pas de « mauvais SEO » non démontré.
- « Site ancien » uniquement par indices objectifs (jamais par Ollama). Lighthouse/PageSpeed seulement pour les prospects présélectionnés ; c'est un signal, jamais suffisant seul.
- Ollama : très secondaire, jamais source de vérité.

## Chaînes et contacts
- Chaîne : plusieurs signaux (enseigne, taille, établissements, même enseigne ailleurs, pages « nos restaurants », liste de localités, schema.org multi-lieux) → `chainConfidence` ; NATIONAL (cap 45), NETWORK (55), FRANCHISE (61 : à examiner, jamais ignorée). Un nom courant (« Chez Jules ») n'est jamais une chaîne sur le seul nom.
- Contacts : `tel:`, `mailto:`, JSON-LD, microdata, formulaire ; téléphones FR normalisés (jamais une suite de chiffres quelconque) ; e-mails GENERIC_BUSINESS / PERSONAL_BUSINESS / UNCERTAIN ; JAMAIS d'e-mail fabriqué. `contactEvidence[]` + confiance ; OSM signalé comme tel.

## Sources
- SIRENE (Recherche d'Entreprises) : actifs et diffusables seulement, dédup SIRET. BODACC : signal de nouvelle entreprise, jamais un besoin de site. OSM : complémentaire, jamais Nominatim public en masse.
- Contacts : uniquement publics et professionnels, avec `contactSourceUrl` et `contactDiscoveredAt`. Pas de particuliers.
- Ne jamais contourner captcha, connexion, anti-bot ni limite d'API.

## Score, plafonds et contact
- Deux scores : `commercialPotentialScore` (intérêt apparent) et `dataConfidenceScore` (qualité des données). Score final = brut plafonné ; chaque plafond est expliqué (« Score brut 86 → plafond 74 : raison »).
- « Très bon » exige : fiabilité ≥ 70, besoin OBJECTIVEMENT observable (jamais « site non trouvé » seul), contact exploitable, activité pertinente. « À contacter » exige un contact professionnel ou un canal officiel. Fiabilité < 40 : au mieux « À examiner ».
- Entreprise récente : signal (5 pts max), jamais une preuve de besoin. Budget d'un prospect à froid : inconnu, jamais estimé. Géolocalisation douteuse : distance ignorée. Commune homonyme sans code postal : campagne refusée avec les alternatives.
- Aucun envoi automatique : file « À contacter », notes, brouillon fondé sur des faits réels, validé par l'utilisateur. `local_do_not_contact` et `local_bad_sites` survivent au reset.
- Erreurs : taxonomie (`SEARCH_*`, `SITE_*`, `AUDIT_PARSE_ERROR`, `SOURCE_UNAVAILABLE`, `GEO_FAILED`…), reprise avec backoff (`next_retry_at`), abandon visible ; un prospect en échec n'arrête jamais la campagne ; une étape facultative (Lighthouse, revérification) n'échoue jamais la campagne. Lighthouse en échec ⇒ performance INCONNUE, jamais 0.
