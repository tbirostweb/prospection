"""État de santé en une commande :  python -m worker.healthcheck [--quiet]

Vérifie : base de données (CRITIQUE), SearXNG, moteurs de recherche, campagnes, dernier passage du worker local. Code de sortie 1 si la base est
injoignable — utilisable comme HEALTHCHECK Docker (`--quiet`). Une recherche indisponible n'est PAS critique : le worker attend et reprend.
"""
from __future__ import annotations

import sys

import httpx

from . import db
from .config import SEARXNG_URL
from .local import health


def _probe(url: str) -> tuple[bool, str]:
    try:
        r = httpx.get(url, timeout=5)
        return r.status_code < 500, f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def main() -> int:
    quiet = "--quiet" in sys.argv
    lines: list[str] = []
    try:
        with db.connect() as conn:
            lines.append("✅ base de données : connexion OK")
            engines = health.engines_status(conn)
            camps = db.fetch_all(conn, "SELECT name, status, last_run_at, last_error FROM local_campaigns WHERE enabled=1 ORDER BY id DESC LIMIT 10")
            last = db.fetch_one(conn, "SELECT MAX(started_at) AS at FROM local_run_metrics")["at"]
            problems = health.degradations(conn)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ base de données injoignable : {exc}")
        return 1
    ok, info = _probe(f"{SEARXNG_URL}/")
    lines.append(f"{'✅' if ok else '⚠️ '} SearXNG ({SEARXNG_URL}) : {info}")
    for e in engines:
        lines.append(f"{'✅' if e['state'] == 'ok' else '⚠️ '} moteur {e['engine']} : {e['state']} · santé {e['health']}")
    for c in camps:
        lines.append(f"{'⚠️ ' if c['last_error'] else '✅'} campagne « {c['name']} » : {c['status']}{' — ' + c['last_error'] if c['last_error'] else ''}")
    lines.append(f"🕒 dernier passage du worker local : {last or 'jamais'}")
    lines += [f"{'❌' if p['level'] == 'critical' else '⚠️ '} {p['message']}" for p in problems]
    if not quiet:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
