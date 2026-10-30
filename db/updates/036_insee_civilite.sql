-- Civilité publique INSEE et cadence partagée entre les passages d'enrichissement.
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN manager_civility VARCHAR(16) NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'manager_civility');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
CREATE TABLE IF NOT EXISTS local_insee_state (
  id TINYINT NOT NULL PRIMARY KEY,
  next_request_at DATETIME(6) NOT NULL,
  paused_until DATETIME(6) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
