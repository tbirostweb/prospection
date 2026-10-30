"""Civilité publique des entrepreneurs individuels, via Sirene 3.11 (facultatif).

Appelé sous le verrou d'enrichissement. La cadence et les pauses HTTP 429
sont conservées en base pour survivre aux passages et aux redémarrages.
Aucun contenu de réponse ni identifiant d'entreprise dans la provenance.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from email.utils import parsedate_to_datetime

import httpx

from .. import db
from . import store

API = "https://api.insee.fr/api-sirene/3.11/siren/"
SOURCE = "insee_sirene_3.11"
INTERVAL_S = 2.01  # au plus 30 départs de requêtes sur 60 secondes


def _limit() -> int:
    try:
        return max(0, int((os.getenv("LOCAL_INSEE_PER_RUN") or "").strip() or "50"))
    except ValueError:
        return 50


def _slot(conn, deadline, now, sleep) -> str | None:
    """Réservation partagée ; transaction courte, jamais de verrou SQL pendant l'attente."""
    db.execute(conn, "INSERT IGNORE INTO local_insee_state (id, next_request_at) VALUES (1, UTC_TIMESTAMP(6))")
    while True:
        if deadline is not None and now() >= deadline:
            conn.commit()
            return "time"
        state = db.fetch_one(conn, """SELECT paused_until > UTC_TIMESTAMP(6) AS paused,
                                      GREATEST(0, TIMESTAMPDIFF(MICROSECOND, UTC_TIMESTAMP(6), next_request_at) / 1000000.0) AS wait_s
                                      FROM local_insee_state WHERE id=1 FOR UPDATE""")
        if state["paused"]:
            conn.commit()
            return "quota"
        wait = float(state["wait_s"])
        if wait <= 0:
            db.execute(conn, "UPDATE local_insee_state SET next_request_at=TIMESTAMPADD(MICROSECOND, %s, UTC_TIMESTAMP(6)) WHERE id=1",
                       (int(INTERVAL_S * 1_000_000),))
            conn.commit()
            return None
        conn.commit()
        if deadline is not None and now() + wait >= deadline:
            return "time"
        sleep(min(wait, 60))


def _retry_after(value: str) -> int:
    try:
        return max(60, int(value))
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            return max(60, int((date - dt.datetime.now(dt.timezone.utc)).total_seconds()) + 1)
        except (TypeError, ValueError, OverflowError):
            return 60


def _civil_status(unit: dict) -> tuple[str | None, str]:
    if unit.get("statutDiffusionUniteLegale") != "O":
        return None, "non_diffusible"
    periods = unit.get("periodesUniteLegale")
    current = [p for p in periods if isinstance(p, dict) and "dateFin" in p and p["dateFin"] is None] if isinstance(periods, list) else []
    if len(current) != 1 or str(current[0].get("categorieJuridiqueUniteLegale")) != "1000":
        return None, "not_individual_entrepreneur"
    civility = {"M": "Monsieur", "F": "Madame"}.get(unit.get("sexeUniteLegale"))
    return civility, "available" if civility else "unavailable"


def run(conn, limit: int | None = None, deadline: float | None = None, *, client=None,
        now=time.monotonic, sleep=time.sleep) -> dict:
    out = {"done": 0, "found": 0, "stop": "nothing", "detail": None}
    key = (os.getenv("INSEE_API_KEY") or "").strip()
    limit = _limit() if limit is None else max(0, limit)
    if not key or not limit:
        return {**out, "stop": "off"}
    # Import différé : pas de dépendance circulaire et aucun travail sans clé.
    from . import enrich
    rows = db.fetch_all(conn, f"""SELECT p.id, p.siren FROM local_prospects p
        WHERE {enrich.BASE} AND NOT {enrich.RUNNING}
        AND (p.manager_civility IS NULL OR TRIM(p.manager_civility)='')
        AND p.siren REGEXP '^[0-9]{{9}}$'
        AND JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.insee') IS NULL
        ORDER BY {enrich.ORDER} LIMIT %s""", (limit,))
    owned = client is None
    client = client if client is not None else httpx.Client(follow_redirects=False, trust_env=False)
    try:
        for row in rows:
            # Défense supplémentaire contre une ligne invalide, sans journaliser son contenu.
            if not re.fullmatch(r"[0-9]{9}", str(row.get("siren") or "")):
                continue
            stop = _slot(conn, deadline, now, sleep)
            if stop:
                out["stop"] = stop
                break
            remaining = 15.0 if deadline is None else min(15.0, deadline - now())
            if remaining <= 0:
                out["stop"] = "time"
                break
            try:
                response = client.get(API + row["siren"], headers={"X-INSEE-Api-Key-Integration": key}, timeout=remaining)
            except httpx.HTTPError:
                out["stop"] = "error"
                break
            if response.status_code == 429:
                pause = _retry_after(response.headers.get("Retry-After", ""))
                db.execute(conn, """UPDATE local_insee_state SET paused_until=TIMESTAMPADD(SECOND, %s, UTC_TIMESTAMP(6)),
                                    next_request_at=GREATEST(next_request_at, TIMESTAMPADD(SECOND, %s, UTC_TIMESTAMP(6))) WHERE id=1""", (pause, pause))
                conn.commit()
                out["stop"] = "quota"
                break
            civility = None
            if response.status_code == 404:
                status = "not_found"
            elif response.status_code == 200:
                try:
                    unit = response.json()["uniteLegale"]
                    if not isinstance(unit, dict) or not unit:
                        raise ValueError
                    civility, status = _civil_status(unit)
                except (ValueError, KeyError, TypeError):
                    out["stop"] = "error"
                    break
            else:
                out["stop"] = "error"
                break
            if civility:
                db.execute(conn, """UPDATE local_prospects SET manager_civility=%s WHERE id=%s
                                    AND (manager_civility IS NULL OR TRIM(manager_civility)='')""", (civility, row["id"]))
            store.set_provenance(conn, row["id"], "insee", {
                "source": SOURCE, "status": status,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            conn.commit()
            out["done"] += 1
            out["found"] += int(bool(civility))
        else:
            out["stop"] = "limit" if len(rows) == limit else "nothing"
    finally:
        if owned:
            client.close()
    return out
