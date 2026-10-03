-- 011 — Barème v4 (projet + compétences d'abord, type de client hors note, budget 10 points)
--       et recherche resserrée (moins de bruit, plus de qualité). Idempotent.

-- 1) Poids du barème : projet 25 · compétences 25 · intention d'achat 20 · budget 10 · fraîcheur 10 · site 5 · lieu 5.
--    (Écrase les poids de la 010 : c'est le but, l'ancien barème donnait 25 points au budget.)
INSERT INTO settings (user_id, skey, value_json)
SELECT id, 'score_weights',
       JSON_OBJECT('project_fit', 25, 'tech_fit', 25, 'purchase_intent', 20, 'budget_value', 10,
                   'freshness', 10, 'existing_site', 5, 'remote_location', 5)
FROM users
ON DUPLICATE KEY UPDATE value_json = VALUES(value_json);

-- 2) Le type de client (PME, particulier…) n'entre plus dans la note : ses poids disparaissent.
DELETE FROM settings WHERE skey = 'client_type_weights';

-- 3) Ton offre couvre aussi la maintenance et la migration de sites : elles comptent comme projets voulus.
INSERT IGNORE INTO preferences (profile_id, kind, value)
SELECT id, 'project_wanted', 'maintenance' FROM profiles;
INSERT IGNORE INTO preferences (profile_id, kind, value)
SELECT id, 'project_wanted', 'migration' FROM profiles;

-- 4) Sources : on garde ce qui est structuré et fiable (Codeur, BOAMP), on coupe ce qui fait du bruit.
--    Rien n'est supprimé : tout se réactive depuis la page Sources.
--    TED = gros marchés européens (ex. un avis à 290 000 € relevé), hors gamme d'un freelance.
UPDATE sources SET enabled = 0 WHERE connector = 'ted';
--    « Réseaux & plateformes » = profils Malt, posts épars : peu de vraies demandes.
UPDATE sources SET enabled = 0 WHERE name = 'Recherche web - reseaux et plateformes';
--    Recherche généraliste : 5 requêtes par passage au lieu de 12, 5 résultats par requête, fenêtre d'une semaine.
UPDATE sources
SET config_json = JSON_SET(COALESCE(config_json, JSON_OBJECT()),
                           '$.queries_per_run', 5, '$.max_results_per_query', 5, '$.time_range', 'week')
WHERE name = 'Recherche web (SearXNG)';
--    BOAMP : seulement les avis récents.
UPDATE sources
SET config_json = JSON_SET(COALESCE(config_json, JSON_OBJECT()), '$.lookback_days', 30, '$.max_results', 50)
WHERE connector = 'boamp';

-- 5) Pool de requêtes : le nouveau générateur (client qui cherche un prestataire) est plus petit.
--    On supprime les requêtes jamais lancées (les utiles seront recréées), on coupe celles déjà lancées
--    qui ramènent des offres d'emploi, des profils de freelances ou des appels d'offres (couverts par BOAMP).
DELETE q FROM search_queries q
JOIN sources s ON s.id = q.source_id
WHERE s.connector = 'websearch' AND q.runs = 0 AND q.family <> 'legacy';

UPDATE search_queries q
JOIN sources s ON s.id = q.source_id
SET q.enabled = 0
WHERE s.connector = 'websearch' AND q.family <> 'legacy'
  AND (q.query LIKE 'recherche développeur%' OR q.query LIKE 'cherche développeur%'
       OR q.query LIKE 'besoin développeur%' OR q.query LIKE 'besoin de développeur%'
       OR q.query LIKE '%webmaster%' OR q.query LIKE '%appel d''offres%' OR q.query LIKE 'consultation refonte%'
       OR q.query LIKE 'freelance % mission entreprise' OR q.query LIKE 'recherche agence web%'
       OR q.family IN ('metier', 'appel_ville', 'appel_region'));
