-- ════════════════════════════════════════════════════════════════════
-- Requêtes web formulées comme une DEMANDE de client, et non comme des
-- mots-clés de prestataire.
--   Avant : « création site internet Lyon », « développeur web freelance Lyon »
--           -> surtout des sites d'agences et des profils de freelances
--              (des concurrents, pas des clients).
--   Après : « cherche prestataire refonte site internet Lyon », « "refaire mon
--           site internet" devis » -> des personnes qui ont un besoin.
-- Rotation : `queries_per_run` requêtes par passage (évite le blocage Google).
-- Idempotent (UPDATE par nom de source).
-- ════════════════════════════════════════════════════════════════════

UPDATE sources
SET config_json = JSON_OBJECT(
  'time_range', 'week',
  'engines', 'google,bing,duckduckgo',
  'max_results_per_query', 10,
  'max_queries', 60,
  'queries_per_run', 15,
  'cities', JSON_ARRAY(
    'Paris', 'Lyon', 'Marseille', 'Bordeaux', 'Toulouse', 'Nantes',
    'Lille', 'Strasbourg', 'Rennes', 'Dijon', 'Besançon'
  ),
  'query_templates', JSON_ARRAY(
    'cherche prestataire refonte site internet {city}',
    'besoin refaire site internet entreprise {city}',
    'recherche freelance création site vitrine {city}'
  ),
  'queries', JSON_ARRAY(
    '"refaire mon site internet" devis',
    '"refonte de mon site" prestataire',
    '"moderniser mon site" entreprise',
    '"mon site internet est vieux"',
    '"site pas responsive" refaire devis',
    '"cherche un développeur" site internet entreprise',
    '"besoin d''un site internet" commerce',
    'site:leboncoin.fr refonte site internet',
    'site:leboncoin.fr création site internet entreprise',
    'site:starofservice.com refonte site internet',
    'site:jemepropose.com création site internet',
    'forum "refaire le site" entreprise prestataire'
  )
)
WHERE name = 'Recherche web (SearXNG)';

-- La 2e recherche (réseaux & plateformes) est déjà formulée côté client :
-- on active seulement la rotation.
UPDATE sources
SET config_json = JSON_SET(config_json, '$.queries_per_run', 6)
WHERE name = 'Recherche web - reseaux et plateformes';
