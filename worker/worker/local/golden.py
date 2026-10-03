"""Cas de régression issus de TES retours (« Mauvais site », « J'ai trouvé le site ») :  python -m worker.local.golden export [--out fichier.json] [--snapshot]

Chaque retour devient un cas : entreprise (instantané), domaine attendu (« J'ai trouvé le site ») ou domaine à NE JAMAIS accepter (« Mauvais site »).
`--snapshot` télécharge en plus les pages du site concerné (tronquées, sans scripts) pour rejouer le cas hors ligne avec `worker.local.evaluate`.
Aucun modèle n'est entraîné : c'est un jeu de test. Les valeurs attendues ne sont jamais modifiées pour améliorer les métriques.
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse

from .. import db, net

MAX_HTML = 60_000


def _strip(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|svg|noscript)\b.*?</\1>", "", html)
    return html[:MAX_HTML]


def snapshot_pages(url: str, fetch=net.try_get) -> dict:
    pages: dict = {}
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
    for u in (origin, origin + "mentions-legales", origin + "contact"):
        got = fetch(u, timeout=8)
        if got is not None and got.ok and got.text:
            pages[u] = {"status": got.status, "html": _strip(got.text), **({"final": got.url} if got.url != u else {})}
    return pages


def export(conn, snapshot: bool = False) -> dict:
    cases = []
    for r in db.fetch_all(conn, "SELECT * FROM local_site_feedback ORDER BY id"):
        company = json.loads(r["company"]) if isinstance(r["company"], (str, bytes)) else (r["company"] or {})
        domain = (urlparse(r["url"] or "").hostname or "").removeprefix("www.") or None
        case = {"id": f"feedback-{r['id']}", "kind": r["kind"], "company": company,
                "official_domain": domain if r["kind"] == "found_site" else None,
                "must_not_accept": domain if r["kind"] == "wrong_site" else None,
                "analysis": json.loads(r["analysis"]) if isinstance(r["analysis"], (str, bytes)) else r["analysis"]}
        if snapshot and r["url"]:
            case["pages"] = snapshot_pages(r["url"])
        cases.append(case)
    return {"cases": cases, "note": "cas issus des retours utilisateur ; expected jamais modifiés pour améliorer les mesures"}


def main(argv: list[str]) -> int:
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    with db.connect() as conn:
        data = export(conn, "--snapshot" in argv)
    text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    if out:
        open(out, "w", encoding="utf-8").write(text)
        print(f"{len(data['cases'])} cas écrits dans {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
