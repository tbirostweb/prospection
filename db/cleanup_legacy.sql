-- NETTOYAGE FACULTATIF ET IRRÉVERSIBLE de l'ancien pipeline d'opportunités (Codeur, BOAMP, TED, recherche web, analyse IA).
-- Ces tables ne sont plus utilisées par l'application. Rien ne les supprime automatiquement : lance ce fichier toi-même, APRÈS une sauvegarde :
--   docker compose exec mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' | gzip > backup.sql.gz
--   docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < db/cleanup_legacy.sql
-- CONSERVÉ : users, settings (tes réglages locaux), schema_migrations, toutes les tables local_*.
-- Ce fichier n'est PAS dans db/updates/ : le worker ne le joue jamais.

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS job_scores;
DROP TABLE IF EXISTS job_analysis;
DROP TABLE IF EXISTS job_sources;
DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS replies;
DROP TABLE IF EXISTS feedback;
DROP TABLE IF EXISTS feedback_log;
DROP TABLE IF EXISTS query_hits;
DROP TABLE IF EXISTS search_queries;
DROP TABLE IF EXISTS candidate_log;
DROP TABLE IF EXISTS run_metrics;
DROP TABLE IF EXISTS source_runs;
DROP TABLE IF EXISTS fetch_cache;
DROP TABLE IF EXISTS site_checks;
DROP TABLE IF EXISTS golden_candidates;
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS sources;
DROP TABLE IF EXISTS skills;
DROP TABLE IF EXISTS preferences;
DROP TABLE IF EXISTS profiles;
SET FOREIGN_KEY_CHECKS = 1;

-- Réglages de l'ancien pipeline dans `settings` (les réglages locaux `local_*` sont conservés).
DELETE FROM settings WHERE skey NOT LIKE 'local\_%';
