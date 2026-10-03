#!/bin/sh
# Validation PRODUCTION à lancer SUR LE VPS (là où tourne Dokploy) :   sh scripts/vps_check.sh  [dossier-du-projet]
# Lecture seule : ne modifie ni la base ni les conteneurs (les builds Docker sont faits avec un tag jetable, supprimé à la fin).
# Colle la sortie complète dans la conversation : rien n'est déclaré « validé en production » avant de l'avoir lue.
set -u
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT" || exit 1
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
ko()   { printf '❌ %s\n' "$*"; FAILED=1; }
FAILED=0
sec()  { printf '\n── %s ──\n' "$*"; }

sec "Machine"
uname -a; echo "CPU : $(nproc 2>/dev/null || echo ?) cœur(s)"
free -m 2>/dev/null | sed -n '1,3p' || true
df -h / 2>/dev/null | sed -n '1,2p' || true
AVAIL=$(df -Pm / | awk 'NR==2{print $4}'); [ "${AVAIL:-0}" -lt 5000 ] && warn "moins de 5 Go libres sur / ($AVAIL Mo)" || ok "espace disque : $AVAIL Mo libres"
MEM=$(free -m 2>/dev/null | awk '/Mem:/{print $7}'); [ -n "${MEM:-}" ] && { [ "$MEM" -lt 1500 ] && warn "RAM disponible faible : $MEM Mo" || ok "RAM disponible : $MEM Mo"; }
uptime 2>/dev/null || true

sec "Docker : builds (tag jetable)"
docker --version || ko "docker introuvable"
if docker build -q -t prospection-check-worker:tmp -f worker/Dockerfile . >/dev/null 2>/tmp/vps_worker_build.log; then ok "build worker"; else ko "build worker (voir /tmp/vps_worker_build.log)"; tail -5 /tmp/vps_worker_build.log; fi
if docker build -q -t prospection-check-web:tmp ./web >/dev/null 2>/tmp/vps_web_build.log; then ok "build web"; else ko "build web (voir /tmp/vps_web_build.log)"; tail -5 /tmp/vps_web_build.log; fi
docker rmi prospection-check-worker:tmp prospection-check-web:tmp >/dev/null 2>&1 || true

sec "Conteneurs de l'application"
docker ps --format '{{.Names}}\t{{.Status}}' | grep -i -E "prospection|mysql|searxng|worker|web" || warn "aucun conteneur reconnu (adapter le filtre)"
W=$(docker ps --format '{{.Names}}' | grep -i worker | head -1); S=$(docker ps --format '{{.Names}}' | grep -i searxng | head -1)
M=$(docker ps --format '{{.Names}}' | grep -i mysql | head -1)
[ -z "$W" ] && { ko "conteneur worker introuvable"; exit 1; }
docker stats --no-stream --format '{{.Name}}  CPU {{.CPUPerc}}  RAM {{.MemUsage}}' | head -12

sec "Réseau Docker / DNS depuis le worker"
docker exec "$W" python - <<'PY' || true
import socket, httpx
for h in ("mysql", "searxng"):
    try: print("✅ DNS interne", h, socket.gethostbyname(h))
    except Exception as e: print("❌ DNS interne", h, e)
for u in ("https://recherche-entreprises.api.gouv.fr/", "https://geo.api.gouv.fr/", "https://bodacc-datadila.opendatasoft.com/", "https://overpass-api.de/api/status"):
    try: print("✅" if httpx.get(u, timeout=10).status_code < 500 else "⚠️ ", u)
    except Exception as e: print("❌", u, type(e).__name__)
PY

sec "SearXNG depuis l'IP du VPS (moteurs réellement utilisables)"
docker exec "$W" python - <<'PY' || true
import os, httpx, time
url = os.getenv("SEARXNG_URL", "http://searxng:8080")
for eng in ("google", "bing", "brave", "qwant", "yahoo", "duckduckgo"):
    t = time.time()
    try:
        d = httpx.get(f"{url}/search", params={"q": '"boulangerie" "Troyes"', "format": "json", "engines": eng, "language": "fr"}, timeout=30).json()
        n, bad = len(d.get("results") or []), d.get("unresponsive_engines") or []
        print(("✅" if n else "⚠️ "), f"{eng:9s} {n:2d} résultats {int((time.time()-t)*1000)} ms", bad if bad else "")
    except Exception as e: print("❌", eng, type(e).__name__, e)
PY

sec "MySQL production (lecture seule)"
if [ -n "$M" ]; then
  docker exec "$M" sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "select version()" 2>/dev/null' && ok "MySQL répond" || ko "MySQL ne répond pas"
  docker exec "$M" sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "select filename from schema_migrations order by filename desc limit 3; select count(*) as prospects_locaux from local_prospects; select engine, health, cooldown_until from local_engine_health;" 2>&1' | head -20
else warn "conteneur MySQL introuvable"; fi

sec "Cron réel dans le worker"
docker exec "$W" crontab -l 2>&1 | grep -v '^[A-Z_]*=' | head -12
docker exec "$W" sh -c 'grep -c . /app/logs/local.log 2>/dev/null && tail -n 8 /app/logs/local.log' || warn "pas encore de /app/logs/local.log (aucun passage du cron local ?)"

sec "Auto-audit applicatif (lecture seule)"
docker exec "$W" python -m worker.audit_system || true
docker exec "$W" python -m worker.healthcheck --quiet && ok "healthcheck worker" || ko "healthcheck worker"
docker exec "$W" python -m worker.reset 2>&1 | head -20   # aperçu : ne modifie RIEN sans --yes

sec "Temps de traitement (dernières campagnes)"
[ -n "$M" ] && docker exec "$M" sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "select started_at, campaign_id, duration_ms, processed, confirmed, probable, not_found, errors from local_run_metrics order by id desc limit 8;" 2>&1'

echo; [ "$FAILED" -eq 0 ] && echo "Aucun échec bloquant détecté (lis quand même les ⚠️)." || echo "Des échecs sont à corriger (❌ ci-dessus)."
exit "$FAILED"
