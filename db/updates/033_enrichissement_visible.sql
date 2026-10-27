-- 033 — Enrichissement des fiches existantes rendu visible sans journaux, et quota Google Places du jour.
--       local_enrich_runs : un état par passage d'enrichissement (worker/local/emailsearch.py) — heure, durée, mode, ce que chaque sous-passage
--       a traité et pourquoi il s'est arrêté (verrou pris, budget de temps, quota, rien à faire). Page Statistiques. Les 500 derniers sont gardés.
--       local_places_daily : requêtes Google Places du jour (jour du quota Google, heure du Pacifique), pour ne jamais dépasser le quota quotidien
--       et garder une réserve aux vérifications des fiches existantes. Aucun contenu Google n'est stocké ici. Idempotent.
CREATE TABLE IF NOT EXISTS local_enrich_runs (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  started_at DATETIME NOT NULL,                 -- UTC
  duration_ms INT NOT NULL DEFAULT 0,
  mode VARCHAR(16) NOT NULL,                    -- « full » (avec moteurs) | « no_engines » | « skipped » (passage pas lancé)
  stopped VARCHAR(160) NULL,                    -- raison d'arrêt principale (texte court)
  steps JSON NULL,                              -- [{key, done, found, stop, ms}]
  KEY idx_enrich_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS local_places_daily (
  day DATE NOT NULL PRIMARY KEY,
  requests INT NOT NULL DEFAULT 0,
  budget INT NULL                               -- plafond du jour écrit par le worker (le web ne voit pas les variables LOCAL_*)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
