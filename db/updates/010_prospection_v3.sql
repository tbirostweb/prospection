-- ════════════════════════════════════════════════════════════════════
-- Prospection v3 — modèle normalisé, budget structuré, déduplication,
-- pool de requêtes, santé des sources, apprentissage du feedback.
-- Idempotent : ALTER gardés par information_schema, CREATE IF NOT EXISTS,
-- UPSERT. Auto-appliqué au déploiement (db/updates/).
-- ════════════════════════════════════════════════════════════════════

-- 1) jobs : budget structuré, client/lieu, dates, dédup, niveau de filtre.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE jobs
       ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT ''web'',
       ADD COLUMN budget_type VARCHAR(16) NULL,
       ADD COLUMN budget_source VARCHAR(16) NULL,
       ADD COLUMN budget_confidence DECIMAL(3,2) NULL,
       ADD COLUMN budget_evidence VARCHAR(255) NULL,
       ADD COLUMN city VARCHAR(128) NULL,
       ADD COLUMN department VARCHAR(3) NULL,
       ADD COLUMN region VARCHAR(64) NULL,
       ADD COLUMN country VARCHAR(64) NULL,
       ADD COLUMN deadline DATETIME NULL,
       ADD COLUMN modified_at DATETIME NULL,
       ADD COLUMN canonical_url VARCHAR(1024) NULL,
       ADD COLUMN url_hash CHAR(32) NULL,
       ADD COLUMN domain VARCHAR(255) NULL,
       ADD COLUMN content_hash CHAR(32) NULL,
       ADD COLUMN filter_level VARCHAR(16) NULL,
       ADD COLUMN filter_signals JSON NULL,
       ADD COLUMN existing_site_url VARCHAR(512) NULL,
       ADD INDEX idx_jobs_url_hash (url_hash),
       ADD INDEX idx_jobs_kind (kind),
       ADD INDEX idx_jobs_deadline (deadline),
       ADD INDEX idx_jobs_department (department)',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'jobs' AND column_name = 'kind'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- Reprise de l'existant : annonces Codeur = demandes de projet, budget déclaré.
UPDATE jobs j
  JOIN job_sources js ON js.job_id = j.id
  JOIN sources s ON s.id = js.source_id
SET j.kind = 'request'
WHERE s.connector = 'codeur' AND j.kind = 'web';

UPDATE jobs SET budget_type = 'UNKNOWN', budget_source = 'UNKNOWN', budget_confidence = 0
WHERE budget_min IS NULL AND budget_max IS NULL AND budget_type IS NULL;

UPDATE jobs SET budget_type = 'FIXED', budget_source = 'EXPLICIT', budget_confidence = 1
WHERE (budget_min IS NOT NULL OR budget_max IS NOT NULL) AND budget_type IS NULL;

-- 2) job_analysis : intention d'achat, valeur estimée, mesures.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE job_analysis
       ADD COLUMN purchase_intent TINYINT UNSIGNED NULL,
       ADD COLUMN estimated_value VARCHAR(16) NULL,
       ADD COLUMN estimated_value_confidence DECIMAL(3,2) NULL,
       ADD COLUMN estimated_value_reason VARCHAR(255) NULL,
       ADD COLUMN urgency VARCHAR(8) NULL,
       ADD COLUMN is_relevant TINYINT(1) NULL,
       ADD COLUMN employment TINYINT(1) NULL,
       ADD COLUMN analysis_ms INT NULL,
       ADD COLUMN used_ai TINYINT(1) NOT NULL DEFAULT 1',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'job_analysis' AND column_name = 'purchase_intent'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- Types de client : passage aux valeurs normalisées (MAJUSCULES).
UPDATE job_analysis SET client_type = CASE client_type
  WHEN 'pme' THEN 'PME' WHEN 'commerce' THEN 'COMMERCE' WHEN 'independant' THEN 'INDEPENDANT'
  WHEN 'startup' THEN 'STARTUP' WHEN 'association' THEN 'ASSOCIATION' WHEN 'agence' THEN 'AGENCE'
  WHEN 'particulier' THEN 'PARTICULIER' WHEN 'grand_compte' THEN 'GRANDE_ENTREPRISE'
  WHEN 'inconnu' THEN 'UNKNOWN' ELSE client_type END
WHERE client_type IS NOT NULL;   -- pas de test de casse : la collation est insensible à la casse ('pme' = 'PME')

-- 3) job_sources : requête à l'origine du résultat.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE job_sources
       ADD COLUMN query_id INT NULL,
       ADD COLUMN source_title VARCHAR(512) NULL,
       ADD INDEX idx_js_query (query_id)',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'job_sources' AND column_name = 'query_id'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 4) sources : fréquence, priorité, santé.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE sources
       ADD COLUMN priority TINYINT NOT NULL DEFAULT 5,
       ADD COLUMN interval_minutes INT NOT NULL DEFAULT 20,
       ADD COLUMN last_success_at DATETIME NULL,
       ADD COLUMN last_duration_ms INT NULL,
       ADD COLUMN last_count INT NULL,
       ADD COLUMN last_error VARCHAR(255) NULL,
       ADD COLUMN consecutive_failures INT NOT NULL DEFAULT 0',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'sources' AND column_name = 'interval_minutes'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 5) Historique de collecte (stats par source et par jour).
CREATE TABLE IF NOT EXISTS source_runs (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  source_id    INT NOT NULL,
  started_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  duration_ms  INT NULL,
  fetched      INT NOT NULL DEFAULT 0,
  new_jobs     INT NOT NULL DEFAULT 0,
  duplicates   INT NOT NULL DEFAULT 0,
  rejected     INT NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'ok',
  error        VARCHAR(255) NULL,
  INDEX idx_runs_source (source_id, started_at),
  FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6) Pool de requêtes de recherche web (rentabilité mesurée par requête).
CREATE TABLE IF NOT EXISTS search_queries (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  source_id      INT NOT NULL,
  query          VARCHAR(255) NOT NULL,
  query_hash     CHAR(32) NOT NULL,
  family         VARCHAR(32) NOT NULL DEFAULT 'custom',
  enabled        TINYINT(1) NOT NULL DEFAULT 1,
  prior          DECIMAL(3,2) NOT NULL DEFAULT 0.50,
  runs           INT NOT NULL DEFAULT 0,
  failures       INT NOT NULL DEFAULT 0,
  results_total  INT NOT NULL DEFAULT 0,
  last_results   INT NULL,
  relevant_total INT NOT NULL DEFAULT 0,
  hot_count      INT NOT NULL DEFAULT 0,
  user_pos       INT NOT NULL DEFAULT 0,
  user_neg       INT NOT NULL DEFAULT 0,
  avg_score      DECIMAL(5,1) NULL,
  last_run_at    DATETIME NULL,
  next_run_at    DATETIME NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_query (source_id, query_hash),
  INDEX idx_queries_due (source_id, enabled, next_run_at),
  FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS query_hits (
  query_id  INT NOT NULL,
  job_id    INT NOT NULL,
  seen_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (query_id, job_id),
  FOREIGN KEY (query_id) REFERENCES search_queries(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7) Analyse légère du site existant du client (cache 30 jours par domaine).
CREATE TABLE IF NOT EXISTS site_checks (
  domain      VARCHAR(255) PRIMARY KEY,
  url         VARCHAR(512) NOT NULL,
  checked_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  data        JSON NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8) Cache de pages téléchargées (24 h) : évite de re-télécharger.
CREATE TABLE IF NOT EXISTS fetch_cache (
  url_hash    CHAR(32) PRIMARY KEY,
  url         VARCHAR(1024) NOT NULL,
  fetched_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  content     MEDIUMTEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 9) Décisions de l'utilisateur, SANS clé étrangère : la purge de l'archive
--    supprime les annonces ignorées, mais l'apprentissage doit les garder.
CREATE TABLE IF NOT EXISTS feedback_log (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  job_id          INT NOT NULL,
  decision        VARCHAR(16) NOT NULL,
  status          VARCHAR(32) NULL,
  final_score     INT NULL,
  verdict         VARCHAR(32) NULL,
  source_names    VARCHAR(255) NULL,
  project_type    VARCHAR(64) NULL,
  client_type     VARCHAR(32) NULL,
  technologies    JSON NULL,
  budget_min      INT NULL,
  budget_max      INT NULL,
  purchase_intent TINYINT UNSIGNED NULL,
  keywords        JSON NULL,
  query_ids       JSON NULL,
  decided_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_feedback_job (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 10) Une seule notification Telegram par annonce, garanti par la base.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE notifications ADD UNIQUE KEY uq_notif_job_channel (job_id, channel)',
    'DO 0')
  FROM information_schema.STATISTICS
  WHERE table_schema = DATABASE() AND table_name = 'notifications'
    AND index_name = 'uq_notif_job_channel'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 11) Réglages : barème v3 (total 100), pénalités, poids par type de client.
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'score_weights',
       JSON_OBJECT('budget_value', 25, 'project_fit', 20, 'client_type', 10,
                   'purchase_intent', 15, 'freshness', 10, 'tech_fit', 10,
                   'existing_site', 5, 'remote_location', 5)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'score_penalties',
       JSON_OBJECT('employment', 40, 'unpaid', 40, 'spam', 30, 'ridiculous_budget', 15,
                   'vague', 5, 'intermediary', 10, 'oversized', 8, 'off_skills', 10, 'old', 5)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'client_type_weights',
       JSON_OBJECT('PME', 1.0, 'COMMERCE', 1.0, 'ARTISAN', 0.95, 'INDEPENDANT', 0.85,
                   'COLLECTIVITE', 0.75, 'STARTUP', 0.7, 'ASSOCIATION', 0.6,
                   'GRANDE_ENTREPRISE', 0.55, 'UNKNOWN', 0.55, 'AGENCE', 0.45,
                   'INTERMEDIAIRE', 0.3, 'PARTICULIER', 0.3)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

-- Apprentissage du feedback : activé ; position de référence pour le tri « proximité ».
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'learning', JSON_OBJECT('enabled', true)
FROM users
ON DUPLICATE KEY UPDATE skey = skey;

INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'home', JSON_OBJECT('department', '', 'region', '')
FROM users
ON DUPLICATE KEY UPDATE skey = skey;

-- 12) Nouvelles sources officielles gratuites (API ouvertes).
INSERT INTO sources (name, connector, enabled, config_json, rate_limit, priority, interval_minutes)
SELECT 'BOAMP - appels d''offres publics', 'boamp', 1,
  JSON_OBJECT('lookback_days', 45, 'max_results', 100),
  30, 3, 120
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM sources WHERE name = 'BOAMP - appels d''offres publics');

INSERT INTO sources (name, connector, enabled, config_json, rate_limit, priority, interval_minutes)
SELECT 'TED - marches europeens (France)', 'ted', 1,
  JSON_OBJECT('countries', JSON_ARRAY('FRA'), 'max_results', 50),
  20, 6, 360
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM sources WHERE name = 'TED - marches europeens (France)');

-- 13) Fréquences par défaut des sources existantes.
UPDATE sources SET interval_minutes = 20, priority = 1 WHERE connector = 'codeur';
UPDATE sources SET interval_minutes = 20, priority = 4 WHERE connector = 'websearch';

-- Les recherches web passent en mode « pool » (requêtes générées et notées).
UPDATE sources
SET config_json = JSON_SET(config_json, '$.pool', true, '$.queries_per_run', 12)
WHERE connector = 'websearch' AND JSON_EXTRACT(config_json, '$.pool') IS NULL;

-- La recherche « généraliste » génère son grand pool de requêtes (villes, régions,
-- technologies, métiers, projets) ; la source « réseaux » garde sa liste fixe.
UPDATE sources
SET config_json = JSON_SET(config_json, '$.generate', true)
WHERE connector = 'websearch' AND name = 'Recherche web (SearXNG)';
