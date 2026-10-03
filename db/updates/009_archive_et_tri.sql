-- ════════════════════════════════════════════════════════════════════
-- Tri plus fin + archivage des annonces ignorées.
--   1) Seuils : À examiner dès 64 (au lieu de 55) pour écarter le tout-venant.
--   2) `is_web_project` : l'IA dit si c'est vraiment un site / une appli web
--      (et non du community management, du SEO seul, de la traduction…).
--   3) `ignored_at` : date d'archivage ; purge automatique après 3 jours.
-- Idempotent. Auto-appliqué au déploiement.
-- ════════════════════════════════════════════════════════════════════

INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'verdict_thresholds',
       JSON_OBJECT('PRIORITAIRE', 85, 'A_POSTULER', 70, 'A_EXAMINER', 64)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

SET @add_web := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE job_analysis ADD COLUMN is_web_project TINYINT(1) NULL', 'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'job_analysis'
    AND column_name = 'is_web_project'
);
PREPARE s FROM @add_web; EXECUTE s; DEALLOCATE PREPARE s;

SET @add_ignored := (
  SELECT IF(COUNT(*) = 0,
    'ALTER TABLE jobs ADD COLUMN ignored_at DATETIME NULL, ADD INDEX idx_jobs_ignored_at (ignored_at)',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE table_schema = DATABASE() AND table_name = 'jobs'
    AND column_name = 'ignored_at'
);
PREPARE s FROM @add_ignored; EXECUTE s; DEALLOCATE PREPARE s;

-- Les annonces déjà ignorées avant cette migration : on démarre leur compte à rebours maintenant.
UPDATE jobs SET ignored_at = UTC_TIMESTAMP() WHERE status = 'ignored' AND ignored_at IS NULL;
