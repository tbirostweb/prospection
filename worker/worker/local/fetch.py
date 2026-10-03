"""Téléchargement de pages de sites d'entreprises avec la CAUSE de l'échec (taxonomie d'erreurs), sans jamais lever."""
from __future__ import annotations

import socket
import threading

import httpx

from .. import net
from ..config import log
from . import errors


def classify_fetch_error(exc: BaseException) -> str:
    if isinstance(exc, net.UnsafeURL):
        return errors.SITE_DNS_ERROR if "DNS" in str(exc) else errors.SITE_BLOCKED
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return errors.SITE_TIMEOUT
    if isinstance(exc, (socket.gaierror,)) or "Name or service not known" in str(exc) or "nodename nor servname" in str(exc):
        return errors.SITE_DNS_ERROR
    return errors.SITE_HTTP_ERROR


class Fetcher:
    """`fetch(url) -> net.Fetched | None`. Les échecs sont mémorisés dans `failures` [(catégorie, url)] pour être journalisés."""

    def __init__(self, timeout: float = 8.0):
        self.timeout = httpx.Timeout(timeout, connect=4.0)          # connexion lente = site à ignorer vite ; lecture plafonnée
        self.failures: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def __call__(self, url: str):
        try:
            got = net.safe_get(url, timeout=self.timeout, max_seconds=15.0)
        except (net.UnsafeURL, httpx.HTTPError, OSError) as exc:
            cat = classify_fetch_error(exc)
            with self.lock:
                self.failures.append((cat, url))
            log.info("[local] %s (%s) : %s", url[:100], cat, exc)
            return None
        if got.status in (401, 403, 429, 503) and not got.text:
            with self.lock:
                self.failures.append((errors.SITE_BLOCKED, url))
        return got

    def drain(self) -> list[tuple[str, str]]:
        out, self.failures = self.failures, []
        return out
