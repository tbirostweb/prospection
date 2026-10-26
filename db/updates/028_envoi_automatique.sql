-- Envoi automatique des e-mails de prospection (désactivé par défaut : réglage `local_auto_send`, absent = désactivé).
--   auto_state     : où en est la séquence automatique du prospect. NULL = non géré par l'envoi automatique (suivi manuel).
--                    queued (en file) · active (premier e-mail parti, relances J+3 / J+7 prévues) · replied (a répondu) · opted_out (refus)
--                    · bounced (adresse invalide) · abandoned (sans réponse à J+14) · stopped (arrêté par toi) · failed (envoi impossible).
--   auto_queued_at : date de mise en file.
-- local_emails : chaque e-mail parti (ou tenté) — premier message et relances, avec son Message-ID pour reconnaître les réponses.
-- local_mail_state : état de la boîte (dernier message lu en IMAP) et des prérequis (DNS, configuration), affichés dans l'onglet Envois.
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN auto_state VARCHAR(12) NULL, ADD COLUMN auto_queued_at DATETIME NULL, ADD INDEX idx_lp_auto (auto_state)', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'auto_state');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
CREATE TABLE IF NOT EXISTS local_emails (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  prospect_id INT NOT NULL,
  kind        VARCHAR(12) NOT NULL,
  to_email    VARCHAR(255) NOT NULL,
  subject     VARCHAR(255) NOT NULL,
  body        TEXT NOT NULL,
  message_id  VARCHAR(255) NOT NULL,
  in_reply_to VARCHAR(255) NULL,
  status      VARCHAR(12) NOT NULL,
  error       VARCHAR(255) NULL,
  sent_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_le_message (message_id),
  INDEX idx_le_prospect (prospect_id),
  INDEX idx_le_sent (sent_at),
  CONSTRAINT fk_le_prospect FOREIGN KEY (prospect_id) REFERENCES local_prospects (id) ON DELETE CASCADE,
  CONSTRAINT chk_le_kind CHECK (kind IN ('first', 'followup1', 'followup2')),
  CONSTRAINT chk_le_status CHECK (status IN ('sent', 'failed', 'bounced'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS local_mail_state (
  id             TINYINT PRIMARY KEY,
  imap_validity  BIGINT NULL,
  imap_last_uid  BIGINT NULL,
  imap_at        DATETIME NULL,
  checks         JSON NULL,
  checks_at      DATETIME NULL,
  last_error     VARCHAR(255) NULL,
  last_error_at  DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
