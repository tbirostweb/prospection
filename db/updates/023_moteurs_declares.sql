-- 023 — Le worker DÉCLARE ses fournisseurs de recherche dans local_engine_health (le web ne voit pas ses variables LOCAL_*) :
--       palier (0 SearXNG gratuit, 1 API gratuite à quota, 2 payant), budget de 24 h, plafond mensuel (0 = aucun), configuré oui/non et
--       raison (« pas de clé » : seulement le NOM de la variable, jamais une clé), date de la déclaration. Idempotent.
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_engine_health ADD COLUMN tier TINYINT NULL, ADD COLUMN daily_budget INT NULL, ADD COLUMN monthly_budget INT NULL, ADD COLUMN configured TINYINT(1) NULL, ADD COLUMN config_note VARCHAR(80) NULL, ADD COLUMN declared_at DATETIME NULL',
  'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_engine_health' AND column_name = 'tier');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
