"""Client HTTP poli pour les API publiques (géocodage, SIRENE, BODACC, PageSpeed…).

  * User-Agent honnête, timeouts courts, cadence limitée par hôte (jamais de rafale) ;
  * 429 / 5xx : UNE nouvelle tentative après `Retry-After` (plafonné), puis abandon — on ne contourne aucune limite ;
  * cache en base (`local_http_cache`) : la même requête n'est jamais refaite avant expiration.
"""
from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlencode, urlparse

import httpx

from .. import db
from ..config import USER_AGENT, log

MIN_INTERVAL_S = {"recherche-entreprises.api.gouv.fr": 0.35, "geo.api.gouv.fr": 0.2, "bodacc-datadila.opendatasoft.com": 0.5}
DEFAULT_INTERVAL_S = 1.0
MAX_RETRY_WAIT_S = 20
_last_call: dict[str, float] = {}
_transport: httpx.BaseTransport | None = None       # point d'injection pour les tests (httpx.MockTransport)


class ApiError(RuntimeError):
    """Réponse inexploitable ou refus persistant d'une API tierce."""


def _throttle(host: str) -> None:
    gap = MIN_INTERVAL_S.get(host, DEFAULT_INTERVAL_S)
    wait = _last_call.get(host, 0.0) + gap - time.monotonic()
    if wait > 0 and _transport is None:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def get_json(url: str, params: dict | None = None, *, conn=None, cache_hours: float = 0, timeout: float = 30.0,
             headers: dict | None = None) -> dict | list:
    """GET JSON avec cadence, une reprise sur 429/5xx et cache optionnel (`conn` requis pour le cache)."""
    key = hashlib.sha1((url + "?" + urlencode(sorted((params or {}).items()), doseq=True)).encode()).hexdigest()
    if conn is not None and cache_hours:
        row = db.fetch_one(conn, """SELECT body FROM local_http_cache WHERE cache_key=%s
                                    AND fetched_at > UTC_TIMESTAMP() - INTERVAL %s MINUTE""", (key, int(cache_hours * 60)))
        if row:
            return json.loads(row["body"])
    host = urlparse(url).hostname or ""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        _throttle(host)
        try:
            with httpx.Client(timeout=timeout, transport=_transport, follow_redirects=True) as client:
                resp = client.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
        except httpx.HTTPError as exc:
            last_exc = exc
            log.info("[local] %s : %s", host, exc)
            continue
        if resp.status_code in (429, 502, 503, 504) and attempt == 1:
            wait = min(MAX_RETRY_WAIT_S, float(resp.headers.get("retry-after", "2") or 2))
            log.warning("[local] %s a répondu %s : nouvelle tentative dans %.0f s", host, resp.status_code, wait)
            if _transport is None:
                time.sleep(wait)
            last_exc = ApiError(f"HTTP {resp.status_code}")
            continue
        if resp.status_code != 200:
            raise ApiError(f"{host} : HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ApiError(f"{host} : réponse non JSON") from exc
        if conn is not None and cache_hours:
            db.execute(conn, """INSERT INTO local_http_cache (cache_key, body) VALUES (%s,%s)
                                ON DUPLICATE KEY UPDATE body=VALUES(body), fetched_at=UTC_TIMESTAMP()""",
                       (key, json.dumps(data, ensure_ascii=False)))
            conn.commit()
        return data
    raise ApiError(f"{host} : {last_exc}")
