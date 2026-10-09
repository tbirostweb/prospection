"""Alertes Telegram sur incidents (source en panne, Ollama down, crash).

Throttle : une même alerte (même `signature`) n'est renvoyée qu'une fois
toutes les 3 heures, pour ne pas te spammer quand un problème persiste.
"""
from __future__ import annotations

import json
import os
import time

from .config import log
from .pipeline import notify

STATE_FILE = os.environ.get("ALERT_STATE", "/app/logs/alert_state.json")
THROTTLE_SECONDS = 3 * 3600


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def alert(signature: str, text: str) -> None:
    """Envoie une alerte si la même n'a pas déjà été envoyée récemment."""
    now = time.time()
    state = _load()
    if now - state.get(signature, 0) < THROTTLE_SECONDS:
        log.info("[alert] '%s' throttlée", signature)
        return
    if notify.send_text(f"⚠️ <b>Prospection</b> — {notify.esc(text)}"):
        state[signature] = now
        _save(state)
