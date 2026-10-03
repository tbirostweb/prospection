"""Lighthouse via l'API PageSpeed Insights (gratuite, clé facultative) — UNIQUEMENT pour les prospects déjà présélectionnés.

Le score Lighthouse est un SIGNAL parmi d'autres : jamais une raison suffisante pour juger un prospect intéressant. Une erreur ou un quota
dépassé (429) rend None : le prospect garde son évaluation légère, rien n'est bloqué.
"""
from __future__ import annotations

import os

from ..config import log
from . import http

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CATEGORIES = ["PERFORMANCE", "SEO", "ACCESSIBILITY", "BEST_PRACTICES"]


def run_pagespeed(url: str, strategy: str = "mobile", key: str | None = None) -> dict | None:
    """{'performance','seo','accessibility','best_practices'} en 0-100, ou None (quota, timeout, site illisible…)."""
    params: dict = {"url": url, "strategy": strategy, "category": CATEGORIES}
    if key := key or os.getenv("PAGESPEED_API_KEY"):
        params["key"] = key
    try:
        data = http.get_json(API, params, timeout=90)
    except http.ApiError as exc:
        log.info("[pagespeed] %s : %s", url[:80], exc)
        return None
    cats = ((data or {}).get("lighthouseResult") or {}).get("categories") or {}
    out = {}
    for name, key_ in (("performance", "performance"), ("seo", "seo"), ("accessibility", "accessibility"), ("best_practices", "best-practices")):
        score = (cats.get(key_) or {}).get("score")
        if score is None:
            return None
        out[name] = int(round(score * 100))
    return out
