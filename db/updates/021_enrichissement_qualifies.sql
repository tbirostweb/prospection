-- 021 — Enrichissement des prospects QUALIFIÉS : réseaux sociaux / professionnels vérifiés (liens en 1 clic sur la fiche), date de l'enrichissement
--       approfondi (recherche du site élargie, réseaux, e-mail, dirigeant). Idempotent.
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_prospects ADD COLUMN social_links JSON NULL, ADD COLUMN deep_enriched_at DATETIME NULL',
  'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'social_links');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- Structures sans intérêt commercial déjà importées : holdings, sièges, SCI / loueurs de biens, syndics, administrations, organismes. Idempotent.
UPDATE local_prospects SET excluded_reason = 'structure sans intérêt commercial pour un site (holding, SCI, administration…)'
 WHERE excluded_reason IS NULL
   AND (naf_code IN ('64.20Z', '70.10Z', '68.20A', '68.20B', '68.32A', '84.11Z')
        OR legal_category LIKE '7%' OR legal_category LIKE '8%' OR legal_category LIKE '654%');
