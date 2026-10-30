#!/usr/bin/env python3
"""Affiche uniquement des statistiques agrégées de réponses; ne conserve aucune donnée."""
from __future__ import annotations

import base64
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path.home() / ".config/prospection/rediger.env"
TABLES = (("Métier", "activity"), ("Angle", "angle"), ("Score", "score"), ("Canal", "channel"))


def read_config(path: Path = ENV_PATH) -> tuple[str, str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:export\s+)?(PROSPECTION_USER|PROSPECTION_PASSWORD|PROSPECTION_URL|APP_USER|APP_PASSWORD|ORIGIN_URL|APP_URL)\s*=\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2).strip("'\"")
    origin = values.get("PROSPECTION_URL") or values.get("ORIGIN_URL") or values.get("APP_URL") or os.environ.get("PROSPECTION_URL", os.environ.get("ORIGIN_URL", ""))
    user = values.get("PROSPECTION_USER") or values.get("APP_USER", "")
    password = values.get("PROSPECTION_PASSWORD") or values.get("APP_PASSWORD", "")
    if not origin or not user or not password:
        raise ValueError("Configuration requise absente (URL d’origine et identifiants).")
    return origin.rstrip("/"), user, password


def fetch_stats(origin: str, user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(origin + "/api/local/reply-stats", headers={
        "Authorization": "Basic " + token, "Origin": origin, "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        import json
        return json.loads(response.read())


def pct(n: int, total: int) -> str:
    return f"{round(100 * n / total)} %" if total else "—"


def recommendations(data: dict) -> list[str]:
    if int(data.get("total", 0)) < 50:
        return []
    candidates = []
    for title, key in TABLES:
        groups = [r for r in data.get("tables", {}).get(key, []) if int(r.get("contacted", 0)) >= 10]
        for i, left in enumerate(groups):
            for right in groups[i + 1:]:
                n1, n2 = int(left["contacted"]), int(right["contacted"])
                rates = (("réponses", int(left["replied"]), int(right["replied"])),
                         ("intérêts", int(left["interested"]), int(right["interested"])))
                for metric, a, b in rates:
                    p1, p2 = a / n1, b / n2
                    gap = abs(p1 - p2)
                    if gap >= 0.10:
                        candidates.append((gap, title, metric, left, right, a, b, n1, n2, p1, p2))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]["name"], item[4]["name"]))
    out, selected_dimensions = [], set()
    for _, title, metric, left, right, a, b, n1, n2, p1, p2 in candidates:
        if len(out) == 3:
            break
        if title in selected_dimensions:
            continue
        if p1 > p2:
            higher, lower = left, right
            high_count, high_n, low_count, low_n = a, n1, b, n2
            high_rate, low_rate = p1, p2
        else:
            higher, lower = right, left
            high_count, high_n, low_count, low_n = b, n2, a, n1
            high_rate, low_rate = p2, p1
        out.append(f"Tester davantage le groupe {higher['name']} par {title.lower()} ({metric} {high_count}/{high_n}, {pct(high_count, high_n)}) "
                   f"que {lower['name']} ({low_count}/{low_n}, {pct(low_count, low_n)}). Écart observé {round((high_rate-low_rate)*100)} points; association descriptive, pas preuve de causalité.")
        selected_dimensions.add(title)
    return out


def render(data: dict) -> str:
    total = int(data.get("total", 0))
    lines = ["Analyse des réponses — envois initiaux effectifs", f"Envois effectifs : {total}"]
    lines.append("Ouvertures : indisponibles (aucun suivi d’ouverture exposé).")
    if total < 50:
        lines.append("Échantillon trop petit (moins de 50 envois au total).")
    lines.append("Les angles et scores sont leurs valeurs actuelles, pas un relevé au moment de l’envoi.")
    for title, key in TABLES:
        lines.extend(("", title + " — envoyés | réponses | taux réponse | intéressés | taux intéressés"))
        for row in data.get("tables", {}).get(key, []):
            sent, replies = int(row.get("contacted", 0)), int(row.get("replied", 0))
            interested = int(row.get("interested", 0))
            rate = pct(replies, sent)
            label = str(row.get("name", "Inconnu"))
            if sent < 10:
                label += " — échantillon trop petit (<10)"
            lines.append(f"{label} : {sent} | {replies} | {rate} | {interested} | {pct(interested, sent)}")
    tips = recommendations(data)
    lines.append("")
    if tips:
        lines.append("Pistes à tester (comparaisons descriptives) :")
        lines.extend(f"- {tip}" for tip in tips)
    else:
        lines.append("Recommandations : aucune comparaison exploitable (il faut au moins 50 envois et deux groupes de 10 par dimension).")
    return "\n".join(lines)


def main() -> int:
    try:
        print(render(fetch_stats(*read_config())))
        return 0
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"Analyse impossible : {type(exc).__name__}.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
