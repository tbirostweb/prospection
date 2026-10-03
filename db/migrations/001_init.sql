-- ════════════════════════════════════════════════════════════════════
-- Prospection — schéma initial (MySQL 8)
-- Exécuté automatiquement au premier démarrage (docker-entrypoint-initdb.d).
-- Les listes (compétences, technos…) sont stockées en colonnes JSON.
-- ════════════════════════════════════════════════════════════════════

-- Utilisateurs
CREATE TABLE users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Profil (config compétences / contraintes)
CREATE TABLE profiles (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    user_id            INT NOT NULL,
    min_budget         INT,
    max_budget         INT,
    max_duration_days  INT,
    max_complexity     INT DEFAULT 5,
    remote_only        TINYINT(1) DEFAULT 1,
    countries          JSON,
    languages          JSON,
    max_job_age_hours  INT DEFAULT 72,
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Compétences (typées par niveau)
CREATE TABLE skills (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    profile_id  INT NOT NULL,
    name        VARCHAR(128) NOT NULL,
    level       VARCHAR(16) NOT NULL,
    UNIQUE KEY uq_skill (profile_id, name),
    CONSTRAINT chk_skill_level CHECK (level IN ('strong','acceptable','forbidden')),
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Préférences (types de projet + mots-clés)
CREATE TABLE preferences (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    profile_id  INT NOT NULL,
    kind        VARCHAR(32) NOT NULL,
    value       VARCHAR(255) NOT NULL,
    UNIQUE KEY uq_pref (profile_id, kind, value),
    CONSTRAINT chk_pref_kind CHECK (kind IN
        ('project_wanted','project_rejected','keyword_positive','keyword_negative')),
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Settings (seuils, poids scoring, notif) — flexible JSON
CREATE TABLE settings (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    skey        VARCHAR(64) NOT NULL,
    value_json  JSON NOT NULL,
    UNIQUE KEY uq_setting (user_id, skey),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Sources / connecteurs
CREATE TABLE sources (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    name         VARCHAR(128) NOT NULL,
    -- `connector` n'est PAS unique : plusieurs sources peuvent partager le même
    -- type (2 recherches web ciblées différemment, plusieurs flux RSS…).
    -- C'est le NOM qui identifie une source.
    connector    VARCHAR(64) NOT NULL,
    UNIQUE KEY uq_sources_name (name),
    enabled      TINYINT(1) DEFAULT 1,
    config_json  JSON,
    rate_limit   INT DEFAULT 30,
    last_run_at  DATETIME,
    last_status  VARCHAR(255)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Jobs (annonce logique, dédupliquée)
CREATE TABLE jobs (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    fingerprint    VARCHAR(64) NOT NULL UNIQUE,
    title          VARCHAR(512) NOT NULL,
    description    MEDIUMTEXT NOT NULL,
    url            VARCHAR(1024),
    company        VARCHAR(255),
    location       VARCHAR(255),
    budget_min     INT,
    budget_max     INT,
    currency       VARCHAR(8) DEFAULT 'EUR',
    published_at   DATETIME,
    first_seen_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    language       VARCHAR(16),
    raw_data       JSON,
    status         VARCHAR(32) NOT NULL DEFAULT 'new',
    stage          VARCHAR(32) NOT NULL DEFAULT 'pending',
    reject_reason  VARCHAR(255),
    analysis_attempts TINYINT NOT NULL DEFAULT 0,
    ignored_at     DATETIME,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_status CHECK (status IN
        ('new','to_review','interested','contacted','replied',
         'call','quote_sent','won','lost','ignored')),
    CONSTRAINT chk_stage CHECK (stage IN ('pending','rejected','analyzed','scored')),
    INDEX idx_jobs_stage (stage),
    INDEX idx_jobs_status (status),
    INDEX idx_jobs_published (published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Apparitions d'une annonce sur les sources (dédup N:1)
CREATE TABLE job_sources (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    job_id       INT NOT NULL,
    source_id    INT NOT NULL,
    source_ref   VARCHAR(512),
    source_url   VARCHAR(1024),
    collected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_job_source (source_id, source_ref),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Analyse IA (1 par job)
CREATE TABLE job_analysis (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    job_id              INT NOT NULL UNIQUE,
    model               VARCHAR(64),
    primary_tech        JSON,
    secondary_tech      JSON,
    project_type        VARCHAR(64),
    matched_skills      JSON,
    missing_core_skills JSON,
    secondary_missing   JSON,
    estimated_time      VARCHAR(64),
    hidden_complexity   TINYINT(1) DEFAULT 0,
    clarity             INT,
    client_quality      INT,
    red_flags           JSON,
    reason              TEXT,
    recommended_angle   TEXT,
    raw_json            JSON,
    is_client_request   TINYINT(1) NULL,
    client_type         VARCHAR(32) NULL,
    has_existing_site   TINYINT(1) NULL,
    remote_ok           TINYINT(1) NULL,
    recurring_potential TINYINT(1) NULL,
    country             VARCHAR(64) NULL,
    is_web_project      TINYINT(1) NULL,
    analyzed_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Scores (1 par job, recalculable)
CREATE TABLE job_scores (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    job_id        INT NOT NULL UNIQUE,
    personal_fit  INT,
    opportunity   INT,
    profitability INT,
    risk          INT,
    complexity    INT,
    final_score   INT,
    verdict       VARCHAR(32),
    breakdown     JSON,
    computed_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_scores_final (final_score),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Réponses commerciales générées (à la demande)
CREATE TABLE replies (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    job_id        INT NOT NULL,
    message       TEXT NOT NULL,
    questions     JSON,
    generated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Feedback utilisateur
CREATE TABLE feedback (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    job_id      INT NOT NULL,
    kind        VARCHAR(32) NOT NULL,
    note        TEXT,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Notifications envoyées (anti-doublon Telegram)
CREATE TABLE notifications (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    job_id    INT NOT NULL,
    channel   VARCHAR(32) NOT NULL DEFAULT 'telegram',
    sent_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload   JSON,
    UNIQUE KEY uq_notif (job_id, channel),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
