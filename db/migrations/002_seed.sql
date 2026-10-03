-- ════════════════════════════════════════════════════════════════════
-- Seed initial (MySQL) : utilisateur, profil, compétences, sources, settings.
-- Modifiable ensuite via l'UI.
-- ════════════════════════════════════════════════════════════════════

INSERT IGNORE INTO users (email) VALUES ('[email protected]');

INSERT INTO profiles (user_id, min_budget, max_budget, max_duration_days,
                      max_complexity, remote_only, countries, languages,
                      max_job_age_hours)
SELECT id, 1000, 20000, 60, 4, 1,
       JSON_ARRAY('France','Remote'), JSON_ARRAY('fr','en'), 168
FROM users WHERE email = '[email protected]';

-- Compétences fortes — stack réel (birostweb.fr) : Vue/PHP/WordPress/Tailwind.
-- On liste des variantes de nommage car l'IA matche en minuscules exact.
INSERT IGNORE INTO skills (profile_id, name, level) VALUES
((SELECT id FROM profiles LIMIT 1), 'HTML', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'CSS', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'JavaScript', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Vue', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Vue.js', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Vue 3', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Vite', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Tailwind', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Tailwind CSS', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'PHP', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'MySQL', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'WordPress', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'WooCommerce', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'ACF', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Elementor', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Node.js', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Docker', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'SEO', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'API REST', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'API', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Responsive', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Figma', 'strong'),
((SELECT id FROM profiles LIMIT 1), 'Site vitrine', 'strong');

-- Compétences acceptables — maîtrisées mais pas au cœur de l'offre commerciale.
INSERT IGNORE INTO skills (profile_id, name, level) VALUES
((SELECT id FROM profiles LIMIT 1), 'TypeScript', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'React', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Next.js', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Python', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Laravel', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Symfony', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'PrestaShop', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Shopify', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Stripe', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'jQuery', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Bootstrap', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Sass', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Git', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Linux', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'VPS', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'Nginx', 'acceptable'),
((SELECT id FROM profiles LIMIT 1), 'CMS', 'acceptable');

-- Technologies refusées — natif mobile, moteurs de jeu, entreprise lourde, crypto.
INSERT IGNORE INTO skills (profile_id, name, level) VALUES
((SELECT id FROM profiles LIMIT 1), 'Java', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Spring', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'C#', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), '.NET', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'C++', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Unity', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Unreal', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Swift', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Kotlin', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Flutter', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'React Native', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Blockchain', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Web3', 'forbidden'),
((SELECT id FROM profiles LIMIT 1), 'Solidity', 'forbidden');

-- Types de projet voulus / refusés (alimente le score project_type ; valeurs =
-- enum project_type de l'IA). On accepte tout le web, on écarte le natif mobile.
INSERT IGNORE INTO preferences (profile_id, kind, value) VALUES
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'site_vitrine'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'ecommerce'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'refonte'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'saas'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'dashboard'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'api'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'automatisation'),
((SELECT id FROM profiles LIMIT 1), 'project_wanted', 'integration_ia'),
((SELECT id FROM profiles LIMIT 1), 'project_rejected', 'mobile');

-- Mots-clés positifs (cible PME/artisans/commerces locaux)
INSERT IGNORE INTO preferences (profile_id, kind, value) VALUES
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'site vitrine'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'wordpress'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'refonte'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'artisan'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'commerce'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'PME'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'TPE'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'restaurant'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'association'),
((SELECT id FROM profiles LIMIT 1), 'keyword_positive', 'indépendant');

-- Mots-clés négatifs (préfiltre)
INSERT IGNORE INTO preferences (profile_id, kind, value) VALUES
((SELECT id FROM profiles LIMIT 1), 'keyword_negative', 'bénévole'),
((SELECT id FROM profiles LIMIT 1), 'keyword_negative', 'non rémunéré'),
((SELECT id FROM profiles LIMIT 1), 'keyword_negative', 'travail gratuit'),
((SELECT id FROM profiles LIMIT 1), 'keyword_negative', 'stage'),
((SELECT id FROM profiles LIMIT 1), 'keyword_negative', 'MLM'),
((SELECT id FROM profiles LIMIT 1), 'keyword_negative', 'pyramidal');

-- Sources MVP
INSERT IGNORE INTO sources (name, connector, enabled, config_json, rate_limit) VALUES
('Codeur.com', 'codeur', 1,
 '{"feed_url":"https://www.codeur.com/projects.rss"}', 20),
('Recherche web (SearXNG)', 'websearch', 1,
 '{"time_range":"week","engines":"google,bing,duckduckgo","max_queries":60,"cities":["Paris","Lyon","Marseille","Bordeaux","Toulouse","Nantes","Lille","Strasbourg","Rennes","Dijon","Besançon"],"query_templates":["création site internet {city}","refonte site web {city}","création site vitrine {city} artisan","développeur web freelance {city}","création boutique en ligne {city}"],"queries":["PME cherche développeur site web","artisan besoin site internet devis","création site WordPress freelance","freelance développeur Vue.js PHP","refonte site WordPress prestataire","création boutique WooCommerce freelance","site:leboncoin.fr création site internet","site:starofservice.com création site web","site:jemepropose.com création site internet"]}', 15),
('Recherche web - reseaux et plateformes', 'websearch', 1,
 '{"time_range":"month","engines":"google,bing,duckduckgo","max_queries":30,"queries":["site:linkedin.com/posts recherche développeur site web","site:facebook.com cherche développeur site internet","site:malt.fr mission création site vitrine","je cherche un développeur site vitrine","recherche prestataire refonte site internet","startup cherche développeur site web freelance","association cherche création site internet","nouvelle entreprise création site internet devis"]}', 15);

-- Settings : seuils de verdict, seuil de notification, poids du scoring
INSERT IGNORE INTO settings (user_id, skey, value_json)
SELECT id, 'verdict_thresholds',
       '{"PRIORITAIRE":85,"A_POSTULER":70,"A_EXAMINER":64}'
FROM users WHERE email = '[email protected]';

INSERT IGNORE INTO settings (user_id, skey, value_json)
SELECT id, 'notify_min_score', '85'
FROM users WHERE email = '[email protected]';

INSERT IGNORE INTO settings (user_id, skey, value_json)
SELECT id, 'score_weights',
       '{"stack_fit":25,"project_type":15,"budget":15,"freshness":15,"complexity_fit":10,"clarity":10,"client_quality":5,"risk_penalty":5}'
FROM users WHERE email = '[email protected]';
