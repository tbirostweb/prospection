-- 017 — Alertes Telegram des prospects locaux : un prospect n'est notifié qu'une fois. Idempotent.
SET @q := (SELECT IF(COUNT(*) = 0, 'ALTER TABLE local_prospects ADD COLUMN notified_at DATETIME NULL', 'DO 0')
           FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'notified_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
