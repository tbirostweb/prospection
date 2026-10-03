-- 022 — Moteurs de recherche par paliers (gratuit puis payant) : compteur MENSUEL par fournisseur (plafond mensuel optionnel du payant,
--       quota mensuel gratuit des API) et date de la dernière requête témoin (au plus une par jour et par moteur). Idempotent.
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_engine_health ADD COLUMN month_start DATE NULL, ADD COLUMN month_requests INT NOT NULL DEFAULT 0, ADD COLUMN last_canary_at DATETIME NULL',
  'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_engine_health' AND column_name = 'month_start');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
