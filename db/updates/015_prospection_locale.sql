-- 015 — Prospection LOCALE proactive : pipeline SÉPARÉ des opportunités (Codeur, BOAMP, web).
--       LOCAL_PROSPECT ≠ HOT_OPPORTUNITY : aucune table d'opportunités n'est touchée. Idempotent (rejouable sans erreur).

-- 1) Campagnes : une zone (ville + rayon) et des activités à explorer. Lancée depuis l'interface (statut « queued »),
--    exécutée par le worker (`python -m worker.local.run`), par étapes reprenables.
CREATE TABLE IF NOT EXISTS local_campaigns (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  name              VARCHAR(160) NOT NULL,
  city              VARCHAR(128) NOT NULL,
  postal_code       VARCHAR(10) NULL,
  department        VARCHAR(3) NULL,
  commune_code      VARCHAR(5) NULL,
  latitude          DECIMAL(9,6) NULL,
  longitude         DECIMAL(9,6) NULL,
  radius_km         DECIMAL(5,1) NOT NULL DEFAULT 10,
  activities        JSON NOT NULL,
  max_companies     INT NOT NULL DEFAULT 300,
  status            VARCHAR(12) NOT NULL DEFAULT 'idle',
  enabled           TINYINT(1) NOT NULL DEFAULT 1,
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_run_at       DATETIME NULL,
  last_finished_at  DATETIME NULL,
  last_error        VARCHAR(255) NULL,
  stats             JSON NULL,
  INDEX idx_lc_status (status),
  CONSTRAINT chk_lc_status CHECK (status IN ('idle', 'queued', 'running', 'done', 'error'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2) Prospects locaux : UNE ligne par entreprise (clé SIRET, ou empreinte prudente nom+adresse+ville sans SIRET).
CREATE TABLE IF NOT EXISTS local_prospects (
  id                        INT AUTO_INCREMENT PRIMARY KEY,
  siren                     VARCHAR(9) NULL,
  siret                     VARCHAR(14) NULL,
  fingerprint               VARCHAR(64) NOT NULL,
  company_name              VARCHAR(255) NOT NULL,
  trade_name                VARCHAR(255) NULL,
  activity_key              VARCHAR(40) NULL,
  activity_label            VARCHAR(160) NULL,
  naf_code                  VARCHAR(8) NULL,
  address                   VARCHAR(255) NULL,
  city                      VARCHAR(128) NULL,
  postal_code               VARCHAR(10) NULL,
  department                VARCHAR(3) NULL,
  latitude                  DECIMAL(9,6) NULL,
  longitude                 DECIMAL(9,6) NULL,
  distance_km               DECIMAL(6,1) NULL,
  company_created_at        DATE NULL,
  legal_category            VARCHAR(8) NULL,
  company_size              VARCHAR(4) NULL,
  employee_range            VARCHAR(4) NULL,
  establishments_open       INT NULL,
  is_chain                  TINYINT(1) NOT NULL DEFAULT 0,
  excluded_reason           VARCHAR(160) NULL,
  bodacc                    JSON NULL,
  website_url               VARCHAR(512) NULL,
  website_status            VARCHAR(20) NULL,
  website_confidence        DECIMAL(3,2) NULL,
  website_evidence          JSON NULL,
  phone                     VARCHAR(32) NULL,
  email                     VARCHAR(255) NULL,
  email_kind                VARCHAR(16) NULL,
  contact_page              VARCHAR(512) NULL,
  contact_source_url        VARCHAR(512) NULL,
  contact_discovered_at     DATETIME NULL,
  technical_score           TINYINT UNSIGNED NULL,
  seo_score                 TINYINT UNSIGNED NULL,
  seo_opportunity_score     TINYINT UNSIGNED NULL,
  performance_score         TINYINT UNSIGNED NULL,
  modernization_opportunity VARCHAR(6) NULL,
  modernization_evidence    JSON NULL,
  lighthouse_performance    TINYINT UNSIGNED NULL,
  lighthouse_seo            TINYINT UNSIGNED NULL,
  lighthouse_accessibility  TINYINT UNSIGNED NULL,
  lighthouse_best_practices TINYINT UNSIGNED NULL,
  lighthouse_at             DATETIME NULL,
  issues                    JSON NULL,
  audit                     JSON NULL,
  prospect_score            TINYINT UNSIGNED NULL,
  score_stage               VARCHAR(12) NULL,
  score_details             JSON NULL,
  category                  VARCHAR(12) NULL,
  confidence                DECIMAL(3,2) NULL,
  draft_message             TEXT NULL,
  status                    VARCHAR(16) NOT NULL DEFAULT 'DISCOVERED',
  discovered_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  enriched_at               DATETIME NULL,
  audited_at                DATETIME NULL,
  scored_at                 DATETIME NULL,
  contacted_at              DATETIME NULL,
  last_contacted_at         DATETIME NULL,
  response_status           VARCHAR(16) NULL,
  notes                     TEXT NULL,
  do_not_contact            TINYINT(1) NOT NULL DEFAULT 0,
  UNIQUE KEY uq_lp_siret (siret),
  UNIQUE KEY uq_lp_fingerprint (fingerprint),
  INDEX idx_lp_status (status),
  INDEX idx_lp_score (prospect_score),
  INDEX idx_lp_category (category),
  INDEX idx_lp_siren (siren),
  CONSTRAINT chk_lp_status CHECK (status IN ('DISCOVERED', 'ENRICHED', 'AUDITED', 'QUALIFIED', 'TO_CONTACT', 'CONTACTED',
                                             'REPLIED', 'INTERESTED', 'WON', 'LOST', 'DO_NOT_CONTACT')),
  CONSTRAINT chk_lp_website CHECK (website_status IS NULL OR website_status IN
                                   ('NO_WEBSITE_FOUND', 'WEBSITE_FOUND', 'WEBSITE_UNCERTAIN', 'WEBSITE_UNREACHABLE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3) Une entreprise peut appartenir à plusieurs campagnes ; chaque source qui la voit est mémorisée (SIRENE, BODACC, OSM, recherche web…).
CREATE TABLE IF NOT EXISTS local_prospect_campaigns (
  prospect_id INT NOT NULL,
  campaign_id INT NOT NULL,
  PRIMARY KEY (prospect_id, campaign_id),
  CONSTRAINT fk_lpc_prospect FOREIGN KEY (prospect_id) REFERENCES local_prospects (id) ON DELETE CASCADE,
  CONSTRAINT fk_lpc_campaign FOREIGN KEY (campaign_id) REFERENCES local_campaigns (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS local_prospect_sources (
  prospect_id INT NOT NULL,
  source      VARCHAR(24) NOT NULL,
  source_ref  VARCHAR(255) NULL,
  seen_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (prospect_id, source),
  CONSTRAINT fk_lps_prospect FOREIGN KEY (prospect_id) REFERENCES local_prospects (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4) Liste « ne plus contacter » : SURVIT à toute remise à zéro et exclut l'entreprise de toute campagne future.
CREATE TABLE IF NOT EXISTS local_do_not_contact (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  siret      VARCHAR(14) NULL,
  siren      VARCHAR(9) NULL,
  domain     VARCHAR(255) NULL,
  email      VARCHAR(255) NULL,
  reason     VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_dnc_siret (siret),
  INDEX idx_dnc_siren (siren),
  INDEX idx_dnc_domain (domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5) Cache de réponses d'API tierces (géocodage, BODACC, extraits OSM…) : jamais deux fois la même requête inutilement.
CREATE TABLE IF NOT EXISTS local_http_cache (
  cache_key  CHAR(40) PRIMARY KEY,
  body       MEDIUMTEXT NOT NULL,
  fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_lhc_fetched (fetched_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6) Réglages par défaut du profil local (ville de référence, rayon) : à renseigner dans l'interface, jamais codés en dur.
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'local_home', JSON_OBJECT('city', '', 'postalCode', '', 'defaultRadius', 20)
FROM users
ON DUPLICATE KEY UPDATE value_json = value_json;
