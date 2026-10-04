// Healthcheck du conteneur web : interroge /api/health (base de données joignable) AVEC l'authentification de l'app.
// Les identifiants sont lus dans l'environnement du conteneur ; rien n'est affiché. Code de sortie 0 = sain.
const auth = Buffer.from(`${process.env.APP_USER ?? ""}:${process.env.APP_PASSWORD ?? ""}`, "utf8").toString("base64");
const port = process.env.PORT || 3000;
fetch(`http://127.0.0.1:${port}/api/health`, { headers: { authorization: `Basic ${auth}` }, signal: AbortSignal.timeout(4000) })
  .then((r) => process.exit(r.ok ? 0 : 1))
  .catch(() => process.exit(1));
