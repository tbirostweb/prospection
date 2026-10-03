-- 014 — Barème v5, qualité mesurée par requête et par source, journal des candidats, métriques de production,
--       candidats « golden » proposés depuis l'interface. Idempotent (rejouable sans erreur).

-- 1) Poids du barème v5 : intention 25 · projet 20 · budget/valeur 15 · fraîcheur 15 · client 10 · urgence 5 · technos 5 · lieu 5.
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'score_weights',
       JSON_OBJECT('purchase_intent', 25, 'project_fit', 20, 'budget_value', 15, 'freshness', 15,
                   'client_type', 10, 'urgency', 5, 'tech_fit', 5, 'remote_location', 5)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

-- 1 bis) Réparation : une annonce rejetée n'est jamais « acceptée » (le rescore laissait parfois accepted = 1).
UPDATE jobs SET accepted = 0 WHERE stage = 'rejected' AND accepted = 1;

-- 2) Force des preuves d'une classification (0-100) : un mot-clé seul ne suffit jamais à faire un prospect.
SET @q := (
  SELECT IF(COUNT(*) = 0, 'ALTER TABLE jobs ADD COLUMN evidence_strength TINYINT UNSIGNED NULL', 'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'jobs' AND column_name = 'evidence_strength'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 3) Requêtes : ce que chaque recherche a produit (pages lues, acceptées, à vérifier, rejetées), sa note de
--    performance (0-100) et un éventuel sommeil temporaire. Jamais de suppression définitive automatique.
SET @q := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE search_queries
       ADD COLUMN pages_fetched INT NOT NULL DEFAULT 0,
       ADD COLUMN accepted_total INT NOT NULL DEFAULT 0,
       ADD COLUMN review_total INT NOT NULL DEFAULT 0,
       ADD COLUMN rejected_total INT NOT NULL DEFAULT 0,
       ADD COLUMN perf_score TINYINT UNSIGNED NULL,
       ADD COLUMN sleep_until DATETIME NULL',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'search_queries' AND column_name = 'perf_score'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 4) Sources : note de qualité (0-100) affichée dans la page Sources.
SET @q := (
  SELECT IF(COUNT(*) = 0, 'ALTER TABLE sources ADD COLUMN quality_score TINYINT UNSIGNED NULL', 'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'sources' AND column_name = 'quality_score'
);
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;

-- 5) Journal des candidats : UNE ligne par page examinée (acceptée, à vérifier ou rejetée), sans texte de page.
--    Conservé ~30 jours (purge par le worker) : sert aux statistiques (motifs de rejet, rendement des sources).
CREATE TABLE IF NOT EXISTS candidate_log (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  job_id         INT NULL,
  source_id      INT NULL,
  source         VARCHAR(128) NULL,
  query_id       INT NULL,
  query          VARCHAR(255) NULL,
  domain         VARCHAR(255) NULL,
  url            VARCHAR(1024) NULL,
  page_type      VARCHAR(24) NULL,
  is_buyer       TINYINT(1) NULL,
  buyer_intent   TINYINT UNSIGNED NULL,
  confidence     DECIMAL(3,2) NULL,
  budget         INT NULL,
  score          TINYINT UNSIGNED NULL,
  decision       VARCHAR(8) NOT NULL,
  reject_reason  VARCHAR(255) NULL,
  processing_ms  INT NULL,
  ollama_used    TINYINT(1) NOT NULL DEFAULT 0,
  page_fetched   TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_cl_created (created_at),
  INDEX idx_cl_source (source_id, created_at),
  INDEX idx_cl_query (query_id, created_at),
  INDEX idx_cl_decision (decision, created_at),
  CONSTRAINT chk_cl_decision CHECK (decision IN ('ACCEPT', 'REVIEW', 'REJECT'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6) Métriques par passage du worker (collecte, décisions, Telegram, Ollama, durée).
CREATE TABLE IF NOT EXISTS run_metrics (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  started_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  duration_ms      INT NULL,
  collected        INT NOT NULL DEFAULT 0,
  duplicates       INT NOT NULL DEFAULT 0,
  rejected         INT NOT NULL DEFAULT 0,
  review           INT NOT NULL DEFAULT 0,
  accepted         INT NOT NULL DEFAULT 0,
  telegram_sent    INT NOT NULL DEFAULT 0,
  ollama_calls     INT NOT NULL DEFAULT 0,
  ollama_failures  INT NOT NULL DEFAULT 0,
  INDEX idx_rm_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7) Candidats « golden » : le bouton « Ajouter aux tests » de la fiche crée une ligne PROPOSÉE. Rien n'entre dans
--    tests/golden sans validation explicite, et un attendu (expected_accepted) n'est jamais modifié automatiquement.
CREATE TABLE IF NOT EXISTS golden_candidates (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  job_id             INT NULL,
  url                VARCHAR(1024) NULL,
  title              VARCHAR(512) NULL,
  text               TEXT NULL,
  expected_page_type VARCHAR(24) NOT NULL,
  expected_is_buyer  TINYINT(1) NOT NULL,
  expected_accepted  TINYINT(1) NOT NULL,
  reason             VARCHAR(500) NULL,
  status             VARCHAR(12) NOT NULL DEFAULT 'proposed',
  validated_at       DATETIME NULL,
  INDEX idx_gc_status (status),
  CONSTRAINT chk_gc_status CHECK (status IN ('proposed', 'validated', 'exported'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
