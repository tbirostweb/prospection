-- Stations de lavage auto écartées (pas besoin de site) : même règle que worker/local/naf.py (_CAR_WASH), appliquée aux prospects déjà en base.
-- Seulement le métier auto (NAF 45.*) et les prospects jamais contactés ; le recalcul des scores au démarrage les classe ensuite « Ignorer ».
UPDATE local_prospects SET excluded_reason = 'nom : station de lavage, pas besoin de site', auto_state = IF(auto_state = 'queued', NULL, auto_state)
 WHERE excluded_reason IS NULL AND naf_code LIKE '45.%' AND contacted_at IS NULL AND do_not_contact = 0
   AND (LOWER(company_name) REGEXP '(^|[^a-z])(lavage|car ?wash|wash)([^a-z]|$)'
        OR LOWER(COALESCE(trade_name, '')) REGEXP '(^|[^a-z])(lavage|car ?wash|wash)([^a-z]|$)');
