-- Aucun horodatage inventé pour les anciennes réponses : reprise de la date de collecte.
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN last_prospect_contact_at DATETIME NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'last_prospect_contact_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
