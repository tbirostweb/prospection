"""Pont entre la commande Claude Code /rediger et l'application (bibliothèque standard uniquement ; ce script n'envoie aucun e-mail).

    python3 scripts/rediger.py queue [--limit 5] [--include-drafted] [--email-only | --canal formulaire]
                                                                       -> file de rédaction (faits vérifiés + brouillon de référence), en JSON ;
                                                                          --email-only : seulement les prospects qui peuvent partir par e-mail automatique
                                                                          (quel que soit leur site : il fixe seulement l'angle, refonte / creation / neutre) ;
                                                                          --canal formulaire : fiches sans e-mail avec un formulaire de contact (par score), avec
                                                                          l'URL de la page de contact ; leurs brouillons ne partent jamais seuls
    python3 scripts/rediger.py save ID fichier.json [--no-auto] [--canal formulaire]
                                                                       -> dépose {"subject", "core"} comme brouillon (refusé si un garde-fou échoue)
                                                                          et le met en file d'envoi automatique si le prospect est éligible
                                                                          (l'envoi est fait par le worker, seulement s'il est activé dans Réglages)

Connexion : PROSPECTION_URL (ex. https://prospection.mondomaine.fr), PROSPECTION_USER et PROSPECTION_PASSWORD (= APP_USER / APP_PASSWORD),
lus dans l'environnement ou dans ~/.config/prospection/rediger.env (lignes CLE=valeur, hors du dépôt). Le mot de passe n'est jamais affiché.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

CONFIG = pathlib.Path.home() / ".config" / "prospection" / "rediger.env"


def settings() -> dict[str, str]:
    out: dict[str, str] = {}
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    out.update({k: v for k, v in os.environ.items() if k.startswith("PROSPECTION_")})
    if not out.get("PROSPECTION_URL"):
        sys.exit(f"PROSPECTION_URL manquant : renseigne-le dans {CONFIG} ou dans l'environnement.")
    return out


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    cfg = settings()
    base = cfg["PROSPECTION_URL"].rstrip("/")
    u = urlparse(base)
    headers = {"Accept": "application/json", "Origin": f"{u.scheme}://{u.netloc}"}       # garde anti-CSRF de l'application
    if cfg.get("PROSPECTION_USER") and cfg.get("PROSPECTION_PASSWORD"):
        token = base64.b64encode(f"{cfg['PROSPECTION_USER']}:{cfg['PROSPECTION_PASSWORD']}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        sys.exit(f"Application injoignable ({e.reason}) : vérifie PROSPECTION_URL.")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("queue", "save"):
        print(__doc__)
        return 2
    if argv[0] == "queue":
        limit = argv[argv.index("--limit") + 1] if "--limit" in argv else "5"
        extra = ("&include_drafted=1" if "--include-drafted" in argv else "") + ("&email_only=1" if "--email-only" in argv else "")
        if "--canal" in argv:
            extra += "&canal=" + argv[argv.index("--canal") + 1]
        status, data = call("GET", f"/api/local/agent/queue?limit={int(limit)}{extra}")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if status == 200 else 1
    auto = "--no-auto" not in argv
    canal = None
    if "--canal" in argv:
        i = argv.index("--canal")
        canal = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    argv = [a for a in argv if a != "--no-auto"]
    if len(argv) != 3:
        print("usage : rediger.py save ID fichier.json [--no-auto] [--canal formulaire]", file=sys.stderr)
        return 2
    draft = json.loads(pathlib.Path(argv[2]).read_text(encoding="utf-8"))
    status, data = call("POST", f"/api/local/prospects/{int(argv[1])}/draft",
                        {"source": "rediger", "subject": draft.get("subject", ""), "core": draft.get("core", ""), "auto": auto,
                         **({"canal": canal} if canal else {})})
    if status == 200:
        if data.get("queued"):
            where = "envoi automatique activé" if data.get("autoEnabled") else "envoi automatique DÉSACTIVÉ dans Réglages : rien ne part pour l'instant"
            print(f"brouillon enregistré pour le prospect {int(argv[1])} ({data.get('kind')}) et mis en file d'envoi ({where})")
        else:
            why = f" (pas d'envoi automatique : {data['notQueued']})" if data.get("notQueued") else ""
            print(f"brouillon enregistré pour le prospect {int(argv[1])} ({data.get('kind')}) : à envoyer toi-même{why}")
        return 0
    print(json.dumps({"status": status, **data}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
