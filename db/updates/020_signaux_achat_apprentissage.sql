-- 020 — Probabilité d'ACHAT : signaux (reprise, déménagement, changement de nom… revérifiés au BODACC), budget probable (CA publié, dirigeant),
--       relances J+3 / J+10, type de message envoyé (pour apprendre ce qui marche). Idempotent.
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_prospects
     ADD COLUMN buy_signals JSON NULL,
     ADD COLUMN budget_level VARCHAR(8) NULL,
     ADD COLUMN bodacc_checked_at DATETIME NULL,
     ADD COLUMN revenue BIGINT NULL,
     ADD COLUMN revenue_year SMALLINT NULL,
     ADD COLUMN manager_name VARCHAR(160) NULL,
     ADD COLUMN followups TINYINT UNSIGNED NOT NULL DEFAULT 0,
     ADD COLUMN last_followup_at DATETIME NULL,
     ADD COLUMN draft_kind VARCHAR(24) NULL,
     ADD INDEX idx_lp_followup (status, followups, contacted_at)',
  'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'buy_signals');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- Résultats commerciaux (réponse, client, perdu, sans réponse) : la mémoire de l'apprentissage. SURVIT au bouton « Tout effacer ».
CREATE TABLE IF NOT EXISTS local_outcomes (
  okey           VARCHAR(40) PRIMARY KEY,
  activity_key   VARCHAR(40) NULL,
  city           VARCHAR(128) NULL,
  website_status VARCHAR(20) NULL,
  buy_signals    JSON NULL,
  draft_kind     VARCHAR(24) NULL,
  contacted_at   DATETIME NULL,
  outcome        DECIMAL(3,2) NOT NULL,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
