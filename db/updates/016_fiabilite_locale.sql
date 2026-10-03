-- 016 — Prospection locale : fiabilité. Niveaux de site (CONFIRMED / PROBABLE / UNCERTAIN / NOT_FOUND / UNREACHABLE), couverture de recherche,
--       santé des moteurs, erreurs structurées, reprise avec backoff, deux scores (potentiel / fiabilité des données), retours utilisateur.
--       Idempotent (rejouable sans erreur).

-- 1) Niveaux de site : l'ancien statut FOUND devient CONFIRMED (confiance >= 0,90) ou PROBABLE (0,80 - 0,89).
SET @q := (SELECT IF(COUNT(*) > 0, 'ALTER TABLE local_prospects DROP CHECK chk_lp_website', 'DO 0')
           FROM information_schema.TABLE_CONSTRAINTS WHERE table_schema = DATABASE() AND table_name = 'local_prospects'
           AND constraint_name = 'chk_lp_website');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

UPDATE local_prospects SET website_status = CASE
    WHEN website_status = 'WEBSITE_FOUND' AND website_confidence >= 0.90 THEN 'CONFIRMED'
    WHEN website_status = 'WEBSITE_FOUND' THEN 'PROBABLE'
    WHEN website_status = 'WEBSITE_UNCERTAIN' THEN 'UNCERTAIN'
    WHEN website_status = 'NO_WEBSITE_FOUND' THEN 'NOT_FOUND'
    WHEN website_status = 'WEBSITE_UNREACHABLE' THEN 'UNREACHABLE'
    ELSE website_status END
WHERE website_status LIKE 'WEBSITE%' OR website_status = 'NO_WEBSITE_FOUND';

ALTER TABLE local_prospects ADD CONSTRAINT chk_lp_website CHECK (website_status IS NULL OR website_status IN
    ('CONFIRMED', 'PROBABLE', 'UNCERTAIN', 'NOT_FOUND', 'UNREACHABLE'));

-- 2) Nouvelles colonnes (ajoutées seulement si absentes).
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_prospects
     ADD COLUMN website_original_url VARCHAR(512) NULL,
     ADD COLUMN website_final_url VARCHAR(512) NULL,
     ADD COLUMN canonical_domain VARCHAR(255) NULL,
     ADD COLUMN redirect_chain JSON NULL,
     ADD COLUMN website_search_tried TINYINT UNSIGNED NULL,
     ADD COLUMN website_search_total TINYINT UNSIGNED NULL,
     ADD COLUMN website_absence_confidence DECIMAL(3,2) NULL,
     ADD COLUMN website_checked_at DATETIME NULL,
     ADD COLUMN chain_confidence DECIMAL(3,2) NULL,
     ADD COLUMN chain_name VARCHAR(160) NULL,
     ADD COLUMN chain_kind VARCHAR(16) NULL,
     ADD COLUMN chain_evidence JSON NULL,
     ADD COLUMN contact_confidence DECIMAL(3,2) NULL,
     ADD COLUMN contact_evidence JSON NULL,
     ADD COLUMN phone_confidence DECIMAL(3,2) NULL,
     ADD COLUMN contact_form TINYINT(1) NOT NULL DEFAULT 0,
     ADD COLUMN seo_evidence JSON NULL,
     ADD COLUMN performance_status VARCHAR(8) NULL,
     ADD COLUMN local_presence JSON NULL,
     ADD COLUMN commercial_potential_score TINYINT UNSIGNED NULL,
     ADD COLUMN data_confidence_score TINYINT UNSIGNED NULL,
     ADD COLUMN score_raw TINYINT UNSIGNED NULL,
     ADD COLUMN geo_confidence DECIMAL(3,2) NULL,
     ADD COLUMN business_status_confidence DECIMAL(3,2) NULL,
     ADD COLUMN entity_level VARCHAR(16) NOT NULL DEFAULT ''ESTABLISHMENT'',
     ADD COLUMN sibling_count INT NULL,
     ADD COLUMN osm_data JSON NULL,
     ADD COLUMN provenance JSON NULL,
     ADD COLUMN pipeline_stage VARCHAR(20) NOT NULL DEFAULT ''DISCOVERED'',
     ADD COLUMN attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
     ADD COLUMN next_retry_at DATETIME NULL,
     ADD COLUMN error_category VARCHAR(24) NULL,
     ADD COLUMN last_error VARCHAR(255) NULL,
     ADD COLUMN feedback_reason VARCHAR(24) NULL,
     ADD INDEX idx_lp_stage (pipeline_stage, next_retry_at),
     ADD INDEX idx_lp_domain (canonical_domain)',
  'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'pipeline_stage');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

SET @q := (SELECT IF(COUNT(*) = 0, 'ALTER TABLE local_campaigns ADD COLUMN coverage JSON NULL', 'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_campaigns' AND column_name = 'coverage');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- Les prospects déjà traités gardent leur étape : ils ne sont pas retraités.
UPDATE local_prospects SET pipeline_stage = 'DONE' WHERE website_status IS NOT NULL AND scored_at IS NOT NULL AND pipeline_stage = 'DISCOVERED';

-- 2 bis) Catégories d'e-mail : GENERIC_BUSINESS / PERSONAL_BUSINESS / UNCERTAIN (les anciennes valeurs sont converties).
ALTER TABLE local_prospects MODIFY COLUMN email_kind VARCHAR(20) NULL;
UPDATE local_prospects SET email_kind = CASE email_kind WHEN 'generic' THEN 'GENERIC_BUSINESS' WHEN 'company_domain' THEN 'PERSONAL_BUSINESS'
    WHEN 'freemail' THEN 'UNCERTAIN' ELSE email_kind END WHERE email_kind IN ('generic', 'company_domain', 'freemail');

-- 3) Santé des moteurs de recherche (un moteur en panne est mis en cooldown, jamais « résultat vide »).
CREATE TABLE IF NOT EXISTS local_engine_health (
  engine              VARCHAR(40) PRIMARY KEY,
  enabled             TINYINT(1) NOT NULL DEFAULT 1,
  requests            INT NOT NULL DEFAULT 0,
  successes           INT NOT NULL DEFAULT 0,
  failures            INT NOT NULL DEFAULT 0,
  captchas            INT NOT NULL DEFAULT 0,
  timeouts            INT NOT NULL DEFAULT 0,
  empty_results       INT NOT NULL DEFAULT 0,
  useful_results      INT NOT NULL DEFAULT 0,
  consecutive_failures INT NOT NULL DEFAULT 0,
  consecutive_empty   INT NOT NULL DEFAULT 0,
  window_start        DATETIME NULL,
  window_requests     INT NOT NULL DEFAULT 0,
  avg_ms              INT NULL,
  health              TINYINT UNSIGNED NULL,
  last_success_at     DATETIME NULL,
  last_failure_at     DATETIME NULL,
  last_error          VARCHAR(160) NULL,
  cooldown_until      DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4) Journal d'événements structurés (taxonomie d'erreurs) : alimente Stats / Santé / la détection de dégradation.
CREATE TABLE IF NOT EXISTS local_events (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  category    VARCHAR(32) NOT NULL,
  source      VARCHAR(32) NULL,
  campaign_id INT NULL,
  prospect_id INT NULL,
  detail      VARCHAR(255) NULL,
  INDEX idx_le_at (at),
  INDEX idx_le_cat (category, at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5) Une ligne par passage sur une campagne : sert aux tendances (hier vs aujourd'hui) et au temps moyen par prospect.
CREATE TABLE IF NOT EXISTS local_run_metrics (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  started_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  campaign_id   INT NULL,
  duration_ms   INT NULL,
  processed     INT NOT NULL DEFAULT 0,
  confirmed     INT NOT NULL DEFAULT 0,
  probable      INT NOT NULL DEFAULT 0,
  uncertain     INT NOT NULL DEFAULT 0,
  not_found     INT NOT NULL DEFAULT 0,
  unreachable   INT NOT NULL DEFAULT 0,
  errors        INT NOT NULL DEFAULT 0,
  searches      INT NOT NULL DEFAULT 0,
  searches_empty INT NOT NULL DEFAULT 0,
  INDEX idx_lrm_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6) « Mauvais site » : un domaine rejeté pour un SIRET n'est plus jamais proposé pour lui. Conservé par le reset (retour utilisateur).
CREATE TABLE IF NOT EXISTS local_bad_sites (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  siret      VARCHAR(14) NULL,
  fingerprint VARCHAR(64) NULL,
  domain     VARCHAR(255) NOT NULL,
  reason     VARCHAR(160) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_lbs (siret, domain),
  INDEX idx_lbs_fp (fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7) « J'ai trouvé le site » : site indiqué par l'utilisateur (validé, jamais appris globalement) + analyse de l'échec de la résolution.
CREATE TABLE IF NOT EXISTS local_site_feedback (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  prospect_id INT NULL,
  siret       VARCHAR(14) NULL,
  kind        VARCHAR(16) NOT NULL,
  url         VARCHAR(512) NULL,
  company     JSON NULL,
  analysis    JSON NULL,
  processed_at DATETIME NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_lsf_siret (siret),
  CONSTRAINT chk_lsf_kind CHECK (kind IN ('wrong_site', 'found_site'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8) Dernier rapport de surveillance (écrit par `python -m worker.local.monitor`, lu par la page Santé) : un moniteur muet se voit aussi.
CREATE TABLE IF NOT EXISTS local_health_report (
  id      TINYINT UNSIGNED PRIMARY KEY,
  at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  problems JSON NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
