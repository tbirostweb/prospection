-- 018 — Brouillons créés à la demande (bouton « Créer un brouillon »), veille des nouvelles entreprises. Idempotent.

-- 1) Brouillons : objet + date de création. Les anciens brouillons générés automatiquement par le worker sont retirés
--    (désormais un brouillon n'existe que si TU l'as demandé, et le worker n'y touche plus jamais).
SET @q := (SELECT IF(COUNT(*) = 0, 'ALTER TABLE local_prospects ADD COLUMN draft_subject VARCHAR(200) NULL, ADD COLUMN draft_created_at DATETIME NULL', 'DO 0')
           FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'draft_created_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
UPDATE local_prospects SET draft_message = NULL WHERE draft_created_at IS NULL AND draft_message IS NOT NULL;

-- 2) Veille : une campagne peut surveiller sa zone et signaler les entreprises nouvellement créées (BODACC), avec une alerte Telegram.
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_campaigns ADD COLUMN watch TINYINT(1) NOT NULL DEFAULT 0, ADD COLUMN watch_every_days SMALLINT NOT NULL DEFAULT 30, ADD COLUMN last_watch_at DATETIME NULL',
  'DO 0') FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_campaigns' AND column_name = 'watch');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_prospects ADD COLUMN new_business_at DATETIME NULL, ADD COLUMN new_business_notified_at DATETIME NULL, ADD INDEX idx_lp_newbiz (new_business_at, new_business_notified_at)',
  'DO 0') FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'new_business_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
