-- 035 — Fiches sans e-mail contactées à la main (formulaire de contact, téléphone). Idempotent (colonnes ajoutées seulement si absentes).
--   contact_channel : canal du premier contact — formulaire · telephone (NULL = e-mail ou non précisé) ; sert au tableau « Ce qui répond ».
--   call_attempts / last_call_at : appels restés sans réponse (« À appeler ») ; au 3e, la fiche sort de la liste.
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN contact_channel VARCHAR(12) NULL, ADD COLUMN call_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0, ADD COLUMN last_call_at DATETIME NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'contact_channel');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
