-- ════════════════════════════════════════════════════════════════════
-- V2 — Suppression de la fonctionnalité Portfolio (jugée inutile).
-- La table n'est référencée par aucune clé étrangère : suppression sûre.
-- Auto-appliqué au déploiement (db/updates/).
-- ════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS portfolio_projects;
