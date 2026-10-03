-- ════════════════════════════════════════════════════════════════════
-- V2 — Recherche boostée : rotation de villes sur la recherche web.
-- Chaque template `{city}` est décliné sur toutes les villes -> couverture
-- PME locale démultipliée. Idempotent (UPDATE). Voir db/updates/ (auto au déploiement).
-- ════════════════════════════════════════════════════════════════════

UPDATE sources
SET config_json = JSON_OBJECT(
  'time_range', 'week',
  'engines', 'google,bing,duckduckgo',
  'max_results_per_query', 10,
  'max_queries', 60,
  'cities', JSON_ARRAY(
    'Paris', 'Lyon', 'Marseille', 'Bordeaux', 'Toulouse', 'Nantes',
    'Lille', 'Strasbourg', 'Rennes', 'Dijon', 'Besançon'
  ),
  'query_templates', JSON_ARRAY(
    'création site internet {city}',
    'refonte site web {city}',
    'création site vitrine {city} artisan',
    'développeur web freelance {city}',
    'création boutique en ligne {city}'
  ),
  'queries', JSON_ARRAY(
    'PME cherche développeur site web',
    'artisan besoin site internet devis',
    'création site WordPress freelance',
    'freelance développeur Vue.js PHP',
    'refonte site WordPress prestataire',
    'création boutique WooCommerce freelance',
    'site:leboncoin.fr création site internet',
    'site:starofservice.com création site web',
    'site:jemepropose.com création site internet'
  )
)
WHERE connector = 'websearch';
