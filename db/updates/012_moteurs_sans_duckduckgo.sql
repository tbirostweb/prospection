-- 012 — DuckDuckGo renvoie un CAPTCHA à chaque requête depuis le VPS (constaté sur la page Sources) :
--       il ne rapporte rien et rallonge chaque passage. On ne garde que Google et Bing. Idempotent.
--       (Réactivable depuis la page Sources : paramètre "engines".)
UPDATE sources
SET config_json = JSON_SET(COALESCE(config_json, JSON_OBJECT()), '$.engines', 'google,bing')
WHERE connector = 'websearch'
  AND JSON_UNQUOTE(JSON_EXTRACT(config_json, '$.engines')) LIKE '%duckduckgo%';
