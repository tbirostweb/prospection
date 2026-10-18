-- 026 — Import CSV de prospects depuis l'application (aperçu puis confirmation). Idempotent (rejouable sans erreur).
-- Journal MINIMAL : aucune donnée de la ligne (nom, e-mail, téléphone…) n'est conservée, seulement des empreintes SHA-256,
-- l'issue et la fiche concernée. L'empreinte d'une ligne déjà appliquée rend le réimport du même fichier sans effet.
-- prospect_id SANS clé étrangère : une fiche supprimée (rétention, remise à zéro) laisse le journal lisible, et l'empreinte
-- d'une fiche disparue n'empêche pas de la recréer.
CREATE TABLE IF NOT EXISTS local_imports (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  filename        VARCHAR(160) NULL,
  file_hash       CHAR(64) NOT NULL,
  file_bytes      INT NOT NULL DEFAULT 0,
  separator_char  VARCHAR(1) NULL,
  total_rows      INT NOT NULL DEFAULT 0,
  created_count   INT NOT NULL DEFAULT 0,
  updated_count   INT NOT NULL DEFAULT 0,
  unchanged_count INT NOT NULL DEFAULT 0,
  ignored_count   INT NOT NULL DEFAULT 0,
  error_count     INT NOT NULL DEFAULT 0,
  INDEX idx_li_hash (file_hash),
  INDEX idx_li_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS local_import_rows (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  import_id   INT NOT NULL,
  line_no     INT NOT NULL,
  row_hash    CHAR(64) NULL,
  outcome     VARCHAR(10) NOT NULL,
  prospect_id INT NULL,
  reason      VARCHAR(255) NULL,
  fields      VARCHAR(255) NULL,
  INDEX idx_lir_import (import_id, line_no),
  INDEX idx_lir_hash (row_hash),
  INDEX idx_lir_prospect (prospect_id),
  CONSTRAINT fk_lir_import FOREIGN KEY (import_id) REFERENCES local_imports (id) ON DELETE CASCADE,
  CONSTRAINT chk_lir_outcome CHECK (outcome IN ('CREATED', 'UPDATED', 'UNCHANGED', 'IGNORED', 'SKIPPED', 'ERROR'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
