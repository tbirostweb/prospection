-- 034 — Suivi commercial après une réponse. Idempotent (colonnes ajoutées seulement si absentes).
--   deal_stage / deal_stage_at : où en est l'affaire — REPLIED (a répondu) · MEETING (rendez-vous) · QUOTE (devis envoyé) · WON (gagné) · LOST (perdu) — et depuis quand.
--   remind_at / remind_note    : « à rappeler le » (jour, heure de Paris) et note courte ; vidés quand l'affaire est gagnée ou perdue.
--   reply_kind / reply_excerpt / reply_at : réponse classée par le worker (worker/local/inbox.py) — interested · later · not_interested · replied (neutre) ·
--                                opted_out — avec un extrait de ce qui a été écrit (400 caractères) et la date de réception.
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN deal_stage VARCHAR(8) NULL, ADD COLUMN deal_stage_at DATETIME NULL, ADD COLUMN remind_at DATE NULL, ADD COLUMN remind_note VARCHAR(255) NULL, ADD COLUMN reply_kind VARCHAR(16) NULL, ADD COLUMN reply_excerpt VARCHAR(400) NULL, ADD COLUMN reply_at DATETIME NULL, ADD INDEX idx_lp_remind (remind_at)', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'remind_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
