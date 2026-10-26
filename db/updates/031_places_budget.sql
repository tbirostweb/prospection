-- 031 — Google Places : le worker écrit son plafond mensuel (LOCAL_GOOGLE_PLACES_MONTHLY_BUDGET) dans local_places_usage, pour que la page
--       Statistiques l'affiche (le conteneur web ne voit pas les variables LOCAL_*). NULL = ligne antérieure à cette migration. Idempotent.
SET @q := (SELECT IF(COUNT(*) = 0,
  'ALTER TABLE local_places_usage ADD COLUMN budget INT NULL',
  'DO 0')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_places_usage' AND column_name = 'budget');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
