-- 032 — Google Places comme SOURCE de découverte des campagnes : mémoire des requêtes (mot-clé × commune), pour ne pas refaire la même avant
--       90 jours, et fiches rattachées (nos identifiants de prospects). AUCUN contenu Google n'est stocké ici (ni nom, ni adresse, ni position,
--       ni site) : seulement le mot-clé, le code INSEE de la commune, la date et des compteurs. Vidées par une remise à zéro des données. Idempotent.
CREATE TABLE IF NOT EXISTS local_places_queries (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  keyword VARCHAR(80) NOT NULL,                 -- « plombier » (sans accents ni majuscules)
  commune_code VARCHAR(5) NOT NULL,             -- code INSEE (geo.api.gouv.fr)
  queried_at DATETIME NOT NULL,
  pages INT NOT NULL DEFAULT 0,                 -- requêtes facturées (une par page de 20 résultats)
  results INT NOT NULL DEFAULT 0,
  matched INT NOT NULL DEFAULT 0,               -- rattachés à une entreprise SIRENE
  created INT NOT NULL DEFAULT 0,               -- fiches créées (absentes de la base)
  unmatched INT NOT NULL DEFAULT 0,             -- ignorés : aucune correspondance SIRENE sûre
  UNIQUE KEY uq_places_query (keyword, commune_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS local_places_query_prospects (
  query_id INT NOT NULL,
  prospect_id INT NOT NULL,
  PRIMARY KEY (query_id, prospect_id),
  CONSTRAINT fk_lpqp_query FOREIGN KEY (query_id) REFERENCES local_places_queries (id) ON DELETE CASCADE,
  CONSTRAINT fk_lpqp_prospect FOREIGN KEY (prospect_id) REFERENCES local_prospects (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
