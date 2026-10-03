-- ════════════════════════════════════════════════════════════════════
-- Mise à jour du profil sur le VRAI stack (birostweb.fr).
-- À lancer UNE FOIS sur une base DÉJÀ initialisée (le seed 002 ne rejoue pas).
-- Idempotent : on efface puis on ré-insère. Rejouable sans risque.
--
-- Usage (depuis le VPS, dossier du projet Dokploy) :
--   docker compose exec -T mysql sh -c \
--     'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
--     < db/updates/003_profil_reel.sql
-- ════════════════════════════════════════════════════════════════════

SET @pid := (SELECT id FROM profiles ORDER BY id LIMIT 1);

-- ─── Compétences ────────────────────────────────────────────────────
DELETE FROM skills WHERE profile_id = @pid;

INSERT INTO skills (profile_id, name, level) VALUES
(@pid, 'HTML', 'strong'),
(@pid, 'CSS', 'strong'),
(@pid, 'JavaScript', 'strong'),
(@pid, 'Vue', 'strong'),
(@pid, 'Vue.js', 'strong'),
(@pid, 'Vue 3', 'strong'),
(@pid, 'Vite', 'strong'),
(@pid, 'Tailwind', 'strong'),
(@pid, 'Tailwind CSS', 'strong'),
(@pid, 'PHP', 'strong'),
(@pid, 'MySQL', 'strong'),
(@pid, 'WordPress', 'strong'),
(@pid, 'WooCommerce', 'strong'),
(@pid, 'ACF', 'strong'),
(@pid, 'Elementor', 'strong'),
(@pid, 'Node.js', 'strong'),
(@pid, 'Docker', 'strong'),
(@pid, 'SEO', 'strong'),
(@pid, 'API REST', 'strong'),
(@pid, 'API', 'strong'),
(@pid, 'Responsive', 'strong'),
(@pid, 'Figma', 'strong'),
(@pid, 'Site vitrine', 'strong'),
(@pid, 'TypeScript', 'acceptable'),
(@pid, 'React', 'acceptable'),
(@pid, 'Next.js', 'acceptable'),
(@pid, 'Python', 'acceptable'),
(@pid, 'Laravel', 'acceptable'),
(@pid, 'Symfony', 'acceptable'),
(@pid, 'PrestaShop', 'acceptable'),
(@pid, 'Shopify', 'acceptable'),
(@pid, 'Stripe', 'acceptable'),
(@pid, 'jQuery', 'acceptable'),
(@pid, 'Bootstrap', 'acceptable'),
(@pid, 'Sass', 'acceptable'),
(@pid, 'Git', 'acceptable'),
(@pid, 'Linux', 'acceptable'),
(@pid, 'VPS', 'acceptable'),
(@pid, 'Nginx', 'acceptable'),
(@pid, 'CMS', 'acceptable'),
(@pid, 'Java', 'forbidden'),
(@pid, 'Spring', 'forbidden'),
(@pid, 'C#', 'forbidden'),
(@pid, '.NET', 'forbidden'),
(@pid, 'C++', 'forbidden'),
(@pid, 'Unity', 'forbidden'),
(@pid, 'Unreal', 'forbidden'),
(@pid, 'Swift', 'forbidden'),
(@pid, 'Kotlin', 'forbidden'),
(@pid, 'Flutter', 'forbidden'),
(@pid, 'React Native', 'forbidden'),
(@pid, 'Blockchain', 'forbidden'),
(@pid, 'Web3', 'forbidden'),
(@pid, 'Solidity', 'forbidden');

-- ─── Préférences (types de projet + mots-clés) ──────────────────────
DELETE FROM preferences
WHERE profile_id = @pid
  AND kind IN ('project_wanted', 'project_rejected', 'keyword_positive');

INSERT INTO preferences (profile_id, kind, value) VALUES
(@pid, 'project_wanted', 'site_vitrine'),
(@pid, 'project_wanted', 'ecommerce'),
(@pid, 'project_wanted', 'refonte'),
(@pid, 'project_wanted', 'saas'),
(@pid, 'project_wanted', 'dashboard'),
(@pid, 'project_wanted', 'api'),
(@pid, 'project_wanted', 'automatisation'),
(@pid, 'project_wanted', 'integration_ia'),
(@pid, 'project_rejected', 'mobile'),
(@pid, 'keyword_positive', 'site vitrine'),
(@pid, 'keyword_positive', 'wordpress'),
(@pid, 'keyword_positive', 'refonte'),
(@pid, 'keyword_positive', 'artisan'),
(@pid, 'keyword_positive', 'commerce'),
(@pid, 'keyword_positive', 'PME'),
(@pid, 'keyword_positive', 'TPE'),
(@pid, 'keyword_positive', 'restaurant'),
(@pid, 'keyword_positive', 'association'),
(@pid, 'keyword_positive', 'indépendant');

-- 'stage' en mot-clé négatif (offres de stage, pas des missions freelance)
-- NB : `FROM DUAL` est obligatoire en MySQL pour un SELECT de constantes
-- accompagné d'un WHERE (contrairement à PostgreSQL).
INSERT INTO preferences (profile_id, kind, value)
SELECT @pid, 'keyword_negative', 'stage'
FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM preferences
  WHERE profile_id = @pid AND kind = 'keyword_negative' AND value = 'stage'
);

-- ─── Requêtes web alignées sur le stack réel ────────────────────────
UPDATE sources
SET config_json = '{"time_range":"week","engines":"google,bing,duckduckgo","queries":["recherche freelance création site internet","PME cherche développeur site web","artisan besoin site internet devis","petit commerce création site vitrine","restaurant création site internet freelance","refonte site web recherche prestataire","création site e-commerce freelance","auto-entrepreneur cherche développeur web","création site WordPress freelance","freelance développeur Vue.js PHP","intégration maquette Figma freelance","création boutique WooCommerce freelance","refonte site WordPress prestataire","site:leboncoin.fr création site internet","site:starofservice.com création site web","site:jemepropose.com création site internet","TPE PME cherche création site internet","artisan commerçant besoin site web devis"]}'
WHERE connector = 'websearch';
