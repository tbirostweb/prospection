"""Configuration : variables d'environnement + logging."""
from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from urllib.parse import urlparse


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


DATABASE_URL = env("DATABASE_URL", "mysql://prospection:prospection@localhost:3306/prospection")

SEARXNG_URL = env("SEARXNG_URL", "http://localhost:8080")

TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID", "")
# Surchargeable UNIQUEMENT pour tester contre un faux serveur local (jamais nécessaire en production).
TELEGRAM_API_BASE = env("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

# URL publique de l'app (facultative) : les alertes Telegram contiennent alors un lien vers la fiche du prospect.
APP_URL = (env("APP_URL", "") or "").rstrip("/")

# User-Agent honnête pour le scraping (bonnes pratiques)
USER_AGENT = env(
    "USER_AGENT",
    "ProspectionBot/0.1 (self-hosted; contact via site owner)",
)


# Variables dont la VALEUR ne doit jamais apparaître dans un journal (en plus du jeton Telegram et du mot de passe DB).
SECRET_ENV = re.compile(r"(_API_KEY|_TOKEN|_SECRET|PASSWORD)$")


def _secrets() -> list[str]:
    """Valeurs à ne JAMAIS écrire dans un log : jeton Telegram, mot de passe DB, clés d'API (LOCAL_*_API_KEY, PAGESPEED_API_KEY…),
    mots de passe et secrets présents dans l'environnement. Lu à chaque appel : une variable ajoutée après l'import est couverte."""
    values = [TELEGRAM_BOT_TOKEN, urlparse(DATABASE_URL or "").password]
    values += [v for k, v in os.environ.items() if SECRET_ENV.search(k)]
    # Seuil de longueur : ne pas masquer une valeur triviale partout dans le texte. Les plus longues d'abord (sous-chaînes).
    return sorted({v for v in values if v and len(v) >= 8}, key=len, reverse=True)


def redact(text: str) -> str:
    """Masque les secrets dans un texte (log, trace, message affiché)."""
    for secret in _secrets():
        text = text.replace(secret, "***")
    return text


class _RedactingFormatter(logging.Formatter):
    """Applique `redact` à la ligne FINALE, traces d'exception comprises.

    Nécessaire : les bibliothèques HTTP incluent l'URL dans leurs logs et leurs
    exceptions, et l'API Telegram porte le jeton DANS l'URL (/bot<jeton>/...).
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _redacting_excepthook(exc_type, exc, tb) -> None:
    """Idem pour les exceptions non rattrapées, imprimées hors du logging."""
    sys.stderr.write(redact("".join(traceback.format_exception(exc_type, exc, tb))))


def setup_logging() -> logging.Logger:
    level = env("LOG_LEVEL", "INFO")
    logging.basicConfig(level=level)
    formatter = _RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)
    # httpx logue chaque requête en INFO (URL complète) : bruyant — le poller
    # Telegram seul en produirait 288 par jour. Les erreurs restent visibles.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.excepthook = _redacting_excepthook
    return logging.getLogger("worker")


log = setup_logging()
