-- 013 — Classification structurée des candidats (acheteur ? besoin actuel ? confiance ?) et recherche d'ACHETEUR.
--       Voir .claude/skills/prospect-detection/SKILL.md. Idempotent.

-- 1) Colonnes de classification sur `jobs` (type de page, acheteur, besoin actuel, prestataire web nécessaire,
--    intention d'achat, confiance, preuves, acceptée ?). Ajout gardé : rejouable sans erreur.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE jobs
       ADD COLUMN page_type VARCHAR(24) NULL,
       ADD COLUMN is_buyer TINYINT(1) NULL,
       ADD COLUMN is_current_need TINYINT(1) NULL,
       ADD COLUMN needs_web_provider TINYINT(1) NULL,
       ADD COLUMN buyer_intent TINYINT UNSIGNED NULL,
       ADD COLUMN class_confidence DECIMAL(3,2) NULL,
       ADD COLUMN class_evidence JSON NULL,
       ADD COLUMN accepted TINYINT(1) NOT NULL DEFAULT 0,
       ADD INDEX idx_jobs_accepted (accepted, stage)',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'jobs' AND column_name = 'page_type'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 2) Reprise : les demandes de plateforme et les appels d'offres déjà notés sont des acheteurs par construction.
--    (Les résultats web sont reclassés par le worker au démarrage : `rescore`.)
UPDATE jobs
SET page_type = CASE kind WHEN 'tender' THEN 'PUBLIC_TENDER' ELSE 'MARKETPLACE_LISTING' END,
    is_buyer = 1, is_current_need = 1, needs_web_provider = 1, class_confidence = 0.85, accepted = 1
WHERE stage = 'scored' AND kind IN ('request', 'tender') AND page_type IS NULL;

-- 3) Recherche : le nouveau pool est composé de requêtes d'acheteur (généré par le code). On retire les anciennes
--    requêtes de vendeur (« création site internet », « prestataire wordpress », villes…) et celles de la liste fixe
--    qui visaient des places de marché d'offres de services (leboncoin, jemepropose, starofservice).
DELETE q FROM search_queries q
JOIN sources s ON s.id = q.source_id
WHERE s.connector = 'websearch' AND q.family <> 'legacy';

UPDATE search_queries q
JOIN sources s ON s.id = q.source_id
SET q.enabled = 0
WHERE s.connector = 'websearch' AND q.family = 'legacy' AND s.name = 'Recherche web (SearXNG)';

UPDATE sources
SET config_json = JSON_SET(COALESCE(config_json, JSON_OBJECT()), '$.queries', JSON_ARRAY())
WHERE name = 'Recherche web (SearXNG)';
