-- Vérification des sites déclarés (import CSV) et âge du site.
--   site_year        : année la plus récente de la mention « © » du site (NULL = inconnue). Un site de LOCAL_RECENT_SITE_YEAR (2024) ou après
--                      est récent : la fiche est écartée du ciblage (refonte inutile), même avec un e-mail.
--   site_review      : vérification MANUELLE d'un site douteux — PENDING (à vérifier par toi) · CONFIRMED · REJECTED. NULL = rien à vérifier.
--   site_reviewed_at : date de ta décision.
-- local_site_reviews : chacune de tes décisions avec la confiance et les preuves calculées à ce moment-là ; elles servent à ajuster le seuil
-- de confirmation automatique des prochains sites (worker/local/imported.learn_threshold).
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN site_year SMALLINT NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'site_year');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN site_review VARCHAR(12) NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'site_review');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
SET @q := (SELECT IF(COUNT(*) = 0,
 'ALTER TABLE local_prospects ADD COLUMN site_reviewed_at DATETIME NULL', 'DO 0')
 FROM information_schema.COLUMNS WHERE table_schema = DATABASE() AND table_name = 'local_prospects' AND column_name = 'site_reviewed_at');
PREPARE s FROM @q; EXECUTE s; DEALLOCATE PREPARE s;
CREATE TABLE IF NOT EXISTS local_site_reviews (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  prospect_id INT NULL,
  url         VARCHAR(512) NULL,
  verdict     VARCHAR(12) NOT NULL,
  confidence  DECIMAL(3,2) NULL,
  evidence    JSON NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_lsr_prospect (prospect_id),
  CONSTRAINT chk_lsr_verdict CHECK (verdict IN ('CONFIRMED', 'REJECTED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
