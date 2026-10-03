"""Taxonomie d'erreurs de la prospection locale, politique de reprise (backoff) et journal d'événements.

Une erreur est TOUJOURS classée : elle alimente Stats / Santé et décide de la reprise. Une absence de donnée n'est pas une erreur, et une
erreur n'est jamais transformée en « aucun résultat ».
"""
from __future__ import annotations

import socket

import httpx

from .. import db
from ..config import log

SEARCH_RATE_LIMIT = "SEARCH_RATE_LIMIT"
SEARCH_CAPTCHA = "SEARCH_CAPTCHA"
SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"
SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
SITE_TIMEOUT = "SITE_TIMEOUT"
SITE_DNS_ERROR = "SITE_DNS_ERROR"
SITE_BLOCKED = "SITE_BLOCKED"
SITE_ROBOTS_DENIED = "SITE_ROBOTS_DENIED"
SITE_HTTP_ERROR = "SITE_HTTP_ERROR"
AUDIT_PARSE_ERROR = "AUDIT_PARSE_ERROR"
CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
GEO_FAILED = "GEO_FAILED"
DB_ERROR = "DB_ERROR"
UNEXPECTED = "UNEXPECTED"

# catégorie → (minutes avant reprise au 1er échec, nombre maximal de tentatives). None = pas de reprise automatique.
POLICY: dict[str, tuple[int, int] | None] = {
    SEARCH_RATE_LIMIT: (120, 6), SEARCH_CAPTCHA: (180, 6), SEARCH_UNAVAILABLE: (30, 8), SEARCH_TIMEOUT: (20, 6),
    SITE_TIMEOUT: (60, 3), SITE_DNS_ERROR: (24 * 60, 3), SITE_BLOCKED: (7 * 24 * 60, 2), SITE_ROBOTS_DENIED: None, SITE_HTTP_ERROR: (24 * 60, 2),
    AUDIT_PARSE_ERROR: (12 * 60, 2), CONTACT_NOT_FOUND: None, SOURCE_UNAVAILABLE: (60, 5), GEO_FAILED: (60, 3), DB_ERROR: (10, 5), UNEXPECTED: (60, 3),
}
MAX_BACKOFF_MIN = 24 * 60


class LocalError(RuntimeError):
    def __init__(self, category: str, detail: str = ""):
        super().__init__(f"{category}: {detail}" if detail else category)
        self.category, self.detail = category, detail


class SearchUnavailable(LocalError):
    """La recherche a échoué ou tous les moteurs sont en cooldown : ce n'est PAS « aucun résultat »."""

    def __init__(self, detail: str = "", category: str = SEARCH_UNAVAILABLE):
        super().__init__(category, detail)


def retry_delay_minutes(category: str, attempts: int) -> int | None:
    """Délai avant la prochaine tentative (backoff exponentiel plafonné à 24 h, ou au délai de base s'il est plus long), ou None si la politique interdit de réessayer."""
    pol = POLICY.get(category, POLICY[UNEXPECTED])
    if pol is None or attempts >= pol[1]:
        return None
    return min(max(MAX_BACKOFF_MIN, pol[0]), pol[0] * 2 ** max(0, attempts - 1))


def classify(exc: BaseException) -> str:
    if isinstance(exc, LocalError):
        return exc.category
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return SITE_TIMEOUT
    if isinstance(exc, (socket.gaierror,)):
        return SITE_DNS_ERROR
    if isinstance(exc, httpx.HTTPError):
        return SOURCE_UNAVAILABLE
    if exc.__class__.__name__ in ("OperationalError", "InterfaceError"):
        return DB_ERROR
    return UNEXPECTED


def record(conn, category: str, source: str | None = None, *, campaign_id: int | None = None, prospect_id: int | None = None,
           detail: str | None = None) -> None:
    """Journalise un événement structuré. Ne lève jamais : le journal ne doit pas casser le pipeline."""
    try:
        db.execute(conn, "INSERT INTO local_events (category, source, campaign_id, prospect_id, detail) VALUES (%s,%s,%s,%s,%s)",
                   (category, source, campaign_id, prospect_id, (detail or "")[:250]))
    except Exception as exc:  # noqa: BLE001
        log.warning("[local] événement non journalisé (%s) : %s", category, exc)
