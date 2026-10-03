-- Chaînes, réseaux et franchises : tous écartés (le site est fourni par l'enseigne, rien à leur vendre).
-- Les franchises étaient jusqu'ici « à examiner » : on les écarte et on leur retire leur note. Idempotent.
UPDATE local_prospects SET is_chain = 1, prospect_score = 0, category = 'IGNORER'
 WHERE chain_kind IS NOT NULL AND (is_chain = 0 OR COALESCE(category, '') <> 'IGNORER' OR COALESCE(prospect_score, 1) <> 0);
