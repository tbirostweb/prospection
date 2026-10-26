-- 030 — Google Places (découverte du site d'un prospect « sans site ») : compteur MENSUEL des requêtes, rien d'autre.
--       Aucun contenu Google n'est stocké (seul le place_id va dans la provenance du prospect). Le compteur n'est PAS vidé par une remise à zéro
--       des données : c'est un garde-fou de facturation. Idempotent.
CREATE TABLE IF NOT EXISTS local_places_usage (
  month CHAR(7) NOT NULL PRIMARY KEY,           -- « 2026-10 » (UTC)
  requests INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
