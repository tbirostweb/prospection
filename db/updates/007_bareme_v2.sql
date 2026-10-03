-- ════════════════════════════════════════════════════════════════════
-- Barème v2 — cible : PME / commerces, budget ≥ 1 000 €, site existant à
-- refaire, remote dans toute la France. Voir worker/worker/pipeline/score.py.
-- Idempotent : upserts + ALTER gardés par information_schema (rejouable).
-- ════════════════════════════════════════════════════════════════════

-- 1) Réglages du barème (les anciennes clés de poids n'existent plus).
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'score_weights',
       JSON_OBJECT('budget', 20, 'project_type', 20, 'client_type', 15,
                   'existing_site', 10, 'stack_fit', 10, 'freshness', 10,
                   'remote_fit', 5, 'clarity', 5, 'client_quality', 5)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'verdict_thresholds',
       JSON_OBJECT('PRIORITAIRE', 85, 'A_POSTULER', 70, 'A_EXAMINER', 55)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

-- Alerte Telegram dès « À postuler » (était 85 : quasi inatteignable, max 95).
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'notify_min_score', '70'
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

-- 2) Profil : budget cible 1 000 € ; une demande de PME reste intéressante
--    une semaine (au lieu de 72 h).
UPDATE profiles SET min_budget = 1000, max_job_age_hours = 168;

-- 3) Nouveaux critères extraits par l'IA, stockés en colonnes (affichage/filtres).
--    Un seul ALTER = atomique ; gardé par une colonne témoin pour être rejouable.
SET @alter_analysis := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE job_analysis
       ADD COLUMN is_client_request TINYINT(1) NULL,
       ADD COLUMN client_type VARCHAR(32) NULL,
       ADD COLUMN has_existing_site TINYINT(1) NULL,
       ADD COLUMN remote_ok TINYINT(1) NULL,
       ADD COLUMN recurring_potential TINYINT(1) NULL,
       ADD COLUMN country VARCHAR(64) NULL',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'job_analysis'
    AND column_name = 'is_client_request'
);
PREPARE s FROM @alter_analysis;
EXECUTE s;
DEALLOCATE PREPARE s;

-- 4) File d'analyse : nombre d'essais IA, pour ne pas bloquer la file sur une
--    annonce qui fait systématiquement échouer le modèle.
SET @alter_jobs := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE jobs ADD COLUMN analysis_attempts TINYINT NOT NULL DEFAULT 0',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'jobs'
    AND column_name = 'analysis_attempts'
);
PREPARE s FROM @alter_jobs;
EXECUTE s;
DEALLOCATE PREPARE s;
