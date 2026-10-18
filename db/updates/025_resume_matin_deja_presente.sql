-- Résumé du matin : mémorise le jour où un prospect a été présenté dans « Les meilleurs à contacter ».
-- Un prospect déjà présenté n'y revient plus les jours suivants (il reste dans l'application, vue « À contacter »).
-- NULL = jamais présenté (tous les prospects existants restent éligibles une fois).
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN digest_featured_at DATETIME NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'digest_featured_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
