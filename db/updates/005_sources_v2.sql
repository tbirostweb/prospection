-- ════════════════════════════════════════════════════════════════════
-- V2 — Sources :
--   1) lever la contrainte UNIQUE sur `connector` (elle interdisait d'avoir
--      plusieurs sources du même type : 2 recherches web, N flux RSS…) ;
--   2) supprimer Reddit (403 sur IP de datacenter, hors cible PME FR) ;
--   3) ajouter une 2e recherche web (réseaux sociaux & plateformes).
-- Idempotent. Auto-appliqué au déploiement (db/updates/).
-- ════════════════════════════════════════════════════════════════════

-- 1) UNIQUE(connector) -> UNIQUE(name).
-- MySQL n'a pas de `DROP INDEX IF EXISTS` : on passe par information_schema
-- + requête préparée pour rester rejouable sans erreur.
SET @drop_connector_idx := (
  SELECT IF(COUNT(*) > 0,
            'ALTER TABLE sources DROP INDEX connector',
            'DO 0')
  FROM information_schema.STATISTICS
  WHERE table_schema = DATABASE() AND table_name = 'sources'
    AND index_name = 'connector'
);
PREPARE s FROM @drop_connector_idx;
EXECUTE s;
DEALLOCATE PREPARE s;

SET @add_name_idx := (
  SELECT IF(COUNT(*) = 0,
            'ALTER TABLE sources ADD UNIQUE KEY uq_sources_name (name)',
            'DO 0')
  FROM information_schema.STATISTICS
  WHERE table_schema = DATABASE() AND table_name = 'sources'
    AND index_name = 'uq_sources_name'
);
PREPARE s FROM @add_name_idx;
EXECUTE s;
DEALLOCATE PREPARE s;

-- 2) Reddit : bloqué (403) et hors cible. ON DELETE CASCADE nettoie job_sources.
DELETE FROM sources WHERE connector = 'reddit';

-- 3) 2e recherche web : posts publics LinkedIn/Facebook, plateformes freelance,
-- formulations de demande directe. Fenêtre "month" (ces posts sont plus rares).
INSERT INTO sources (name, connector, enabled, config_json, rate_limit)
SELECT 'Recherche web - reseaux et plateformes', 'websearch', 1,
  JSON_OBJECT(
    'time_range', 'month',
    'engines', 'google,bing,duckduckgo',
    'max_results_per_query', 10,
    'max_queries', 30,
    'queries', JSON_ARRAY(
      'site:linkedin.com/posts recherche développeur site web',
      'site:facebook.com cherche développeur site internet',
      'site:malt.fr mission création site vitrine',
      'je cherche un développeur site vitrine',
      'recherche prestataire refonte site internet',
      'startup cherche développeur site web freelance',
      'association cherche création site internet',
      'nouvelle entreprise création site internet devis'
    )
  ),
  15
-- `FROM DUAL` obligatoire en MySQL pour un SELECT de constantes avec WHERE.
FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM sources WHERE name = 'Recherche web - reseaux et plateformes'
);
