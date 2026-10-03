"""Recherche web multi-moteurs PAR PALIERS avec SANTÉ par fournisseur : un moteur bloqué ne rend jamais toute la recherche vide.

Paliers (le suivant n'est interrogé que si AUCUN fournisseur du palier courant n'a pu répondre : cooldown, budget épuisé, SearXNG en panne) :
  0 — moteurs SearXNG (gratuits, illimités hors rythme humain) ;
  1 — API gratuites à quota : Brave Search (LOCAL_BRAVE_API_KEY), Tavily (LOCAL_TAVILY_API_KEY) ;
  2 — payant, en DERNIER recours et à budget plafonné : Serper (LOCAL_SERPER_API_KEY).
Chaque fournisseur a : enabled, health (0-100), last_success, last_failure, failure_count, cooldown_until, avg_ms, budget de 24 h (et mensuel).
CAPTCHA / 403 / 429 / timeout / suspendu ⇒ le fournisseur est retiré des requêtes (cooldown exponentiel) et les autres continuent.
Si TOUS sont indisponibles : `SearchUnavailable` — jamais une liste vide qui ferait conclure « aucun site ».
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .. import db
from ..config import SEARXNG_URL, USER_AGENT, log
from . import errors
from .errors import SearchUnavailable

DEFAULT_ENGINES = "brave,startpage,mojeek,google,bing,qwant"      # ordre de départ (mesuré depuis une IP résidentielle) ; ensuite trié par santé mesurée
INTERVAL_S = float(os.getenv("LOCAL_ENGINE_INTERVAL_S", "3"))      # par moteur : un rythme humain, pas une rafale (constaté en réel : à 1,5 s, mojeek et startpage se sont mis à répondre 0 en silence)
DAILY_BUDGET = int(os.getenv("LOCAL_ENGINE_DAILY_BUDGET", "300"))    # requêtes par moteur et par fenêtre de 24 h : on ménage les moteurs plutôt que de les brûler
CANARY_QUERY = "restaurant pizza Lyon"                                  # requête banale : un moteur non bloqué y répond toujours
EMPTY_STREAK_LIMIT = 8                                                # N réponses vides d'affilée = blocage silencieux probable
QUERY_ENGINES = 2                 # au plus 2 moteurs SearXNG qui RÉPONDENT par requête : le 2e seulement si le 1er n'a rendu aucun résultat
COOLDOWN_BASE_MIN = {errors.SEARCH_CAPTCHA: 180, errors.SEARCH_RATE_LIMIT: 60, errors.SEARCH_TIMEOUT: 15, errors.SEARCH_UNAVAILABLE: 30}
COOLDOWN_MAX_MIN = 24 * 60
CANARY_EVERY_H = 24               # requête témoin : au plus une par moteur et par 24 h (sinon une série de vides est traitée comme un blocage silencieux)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip() or default)
    except ValueError:
        return default


# Palier 1 — API GRATUITES à quota (aucun scraping). Interrogées SEULEMENT quand aucun moteur SearXNG n'a répondu. Sans clé, rien ne change.
BRAVE_API = "brave_api"
BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_API_KEY = os.getenv("LOCAL_BRAVE_API_KEY", "").strip()
BRAVE_DAILY_BUDGET = _env_int("LOCAL_BRAVE_DAILY_BUDGET", 30)            # ≈ 900 requêtes / mois : sous le quota gratuit
TAVILY_API = "tavily"
TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_API_KEY = os.getenv("LOCAL_TAVILY_API_KEY", "").strip()
TAVILY_DAILY_BUDGET = _env_int("LOCAL_TAVILY_DAILY_BUDGET", 30)
TAVILY_MONTHLY_BUDGET = _env_int("LOCAL_TAVILY_MONTHLY_BUDGET", 1000)    # quota gratuit mensuel de Tavily (0 = pas de plafond mensuel)
# Palier 2 — PAYANT, dernier recours : Serper (résultats Google, le moins cher au millier). Plafonné par jour (et par mois si demandé).
SERPER_API = "serper"
SERPER_API_URL = "https://google.serper.dev/search"
SERPER_API_KEY = os.getenv("LOCAL_SERPER_API_KEY", "").strip()
SERPER_DAILY_BUDGET = _env_int("LOCAL_SERPER_DAILY_BUDGET", 150)
PAID_MONTHLY_BUDGET = _env_int("LOCAL_PAID_MONTHLY_BUDGET", 0)            # plafond mensuel CUMULÉ des fournisseurs payants (0 = aucun plafond mensuel)
PAID_ON_EMPTY = os.getenv("LOCAL_PAID_ON_EMPTY", "0").strip().lower() in ("1", "true", "yes", "oui")   # 1 : un vide du gratuit fait aussi passer aux paliers suivants
API_PROVIDERS = (BRAVE_API, TAVILY_API, SERPER_API)
TIER_LABEL = {0: "gratuit (SearXNG)", 1: "API gratuite à quota", 2: "PAYANT (dernier recours)"}
_transport: httpx.BaseTransport | None = None          # tests


def classify_reason(reason: str) -> str:
    r = (reason or "").lower()
    if "captcha" in r or "recaptcha" in r:
        return errors.SEARCH_CAPTCHA
    if "too many" in r or "429" in r or "rate" in r or "limit" in r:
        return errors.SEARCH_RATE_LIMIT
    if "timeout" in r or "timed out" in r:
        return errors.SEARCH_TIMEOUT
    return errors.SEARCH_UNAVAILABLE                   # 403, suspendu, accès refusé, erreur HTTP, parsing…


def health_score(row: dict) -> int:
    """0-100 d'après succès, erreurs (CAPTCHA pesant plus), timeouts, résultats utiles et temps de réponse. None-safe."""
    req = int(row.get("requests") or 0)
    if req < 3:
        return 60                                       # pas assez d'historique : neutre, on laisse sa chance
    succ, cap, tout = int(row.get("successes") or 0), int(row.get("captchas") or 0), int(row.get("timeouts") or 0)
    useful = int(row.get("useful_results") or 0)
    score = 45 * succ / req + 35 * min(1.0, useful / max(1, succ)) - 20 * cap / req - 10 * tout / req
    avg = row.get("avg_ms")
    score += 20 * (1.0 if avg is None or avg <= 1500 else 0.5 if avg <= 4000 else 0.0)
    score -= min(30, 10 * int(row.get("consecutive_failures") or 0))
    return int(max(0, min(100, round(score))))


@dataclass
class Hit:
    url: str
    title: str = ""
    content: str = ""
    engines: list[str] = field(default_factory=list)

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse
        return (urlparse(self.url).hostname or "").lower().removeprefix("www.")


@dataclass
class Outcome:
    hits: list
    engines_ok: list[str]
    engines_failed: list[tuple[str, str]]              # (moteur, catégorie)

    @property
    def degraded(self) -> bool:
        """Une recherche sans résultat alors qu'un moteur a échoué n'est PAS une preuve d'absence."""
        return bool(self.engines_failed)


NON_WEB_ENGINES = {"wikipedia", "wikidata", "currency", "lingva", "dictzone", "translate", "wolframalpha", "openstreetmap", "duckduckgo definitions", "dictionary", "wiktionary"}
PREFERRED = ("brave", "startpage", "mojeek", "google", "bing", "duckduckgo", "yahoo", "qwant", "google cse")


def discover_enabled(url: str, timeout: float = 10.0) -> list[str]:
    """Moteurs RÉELLEMENT actifs de l'instance SearXNG (`/config`), catégorie « general » — la liste par défaut suppose des moteurs qui peuvent être désactivés."""
    try:
        with httpx.Client(timeout=timeout, transport=_transport) as c:
            cfg = c.get(f"{url}/config", headers={"User-Agent": USER_AGENT}).json()
    except (httpx.HTTPError, ValueError):
        return []
    # Sur le VPS, « general » contient aussi wikipedia, wikidata, currency, lingva, dictzone : des outils, pas des moteurs web (0 résultat pour une entreprise).
    # Un vrai moteur web pagine ; les outils annexes non (et une liste d'exclusion couvre les instances qui n'exposent pas `paging`).
    names = [e["name"] for e in cfg.get("engines", []) if e.get("enabled") and "general" in (e.get("categories") or []) and e.get("name")
             and e.get("paging", True) is not False and e["name"].lower() not in NON_WEB_ENGINES]
    rank = {n: i for i, n in enumerate(PREFERRED)}
    return sorted(names, key=lambda n: rank.get(n, 99))[:8]


# ── Fournisseurs : interface commune ─────────────────────────────────────────────────────────────────
SearchFn = Callable[[str], "tuple[list[Hit], str | None, str | None, int]"]


@dataclass
class Provider:
    """Un fournisseur de recherche : `search(q) -> (hits, catégorie d'erreur | None, message | None, ms)`.

    tier 0 = moteurs SearXNG · tier 1 = API gratuites à quota · tier 2 = payant (dernier recours).
    `daily_budget` : requêtes par fenêtre de 24 h (suivies dans local_engine_health) ; `monthly_budget` : 0 = pas de plafond mensuel propre.
    """
    name: str
    tier: int
    daily_budget: int
    search: SearchFn
    monthly_budget: int = 0


def _api_call(label: str, key_env: str, method: str, url: str, parse, **kwargs) -> tuple[list[Hit], str | None, str | None, int]:
    """Appel d'API JSON normalisé : erreurs classées comme celles de SearXNG (quota → RATE_LIMIT, clé refusée → UNAVAILABLE…)."""
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=20, transport=_transport) as c:
            r = c.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        return [], errors.SEARCH_TIMEOUT, f"{label} : délai dépassé ({exc.__class__.__name__})", int((time.monotonic() - t0) * 1000)
    except httpx.HTTPError as exc:
        return [], errors.SEARCH_UNAVAILABLE, f"{label} injoignable : {exc}", int((time.monotonic() - t0) * 1000)
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code in (402, 429, 432, 433):
        return [], errors.SEARCH_RATE_LIMIT, f"{label} : quota ou crédits épuisés (HTTP {r.status_code})", ms
    if r.status_code in (401, 403, 422):
        return [], errors.SEARCH_UNAVAILABLE, f"{label} : clé refusée (HTTP {r.status_code}) — vérifie {key_env}", ms
    if r.status_code != 200:
        return [], errors.SEARCH_UNAVAILABLE, f"{label} HTTP {r.status_code}", ms
    try:
        data = r.json() or {}
        return parse(data), None, None, ms
    except (ValueError, TypeError, AttributeError, KeyError):
        return [], errors.SEARCH_UNAVAILABLE, f"{label} : réponse illisible", ms


def brave_search(key: str, query: str):
    """API officielle Brave Search (palier 1)."""
    return _api_call("API Brave", "LOCAL_BRAVE_API_KEY", "GET", BRAVE_API_URL,
                     lambda d: [Hit(x["url"], x.get("title") or "", x.get("description") or "", [BRAVE_API])
                                for x in ((d.get("web") or {}).get("results") or [])[:10] if x.get("url")],
                     params={"q": query, "country": "FR", "search_lang": "fr", "count": 10},
                     headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": USER_AGENT})


def tavily_search(key: str, query: str):
    """API Tavily (palier 1, quota mensuel gratuit)."""
    return _api_call("API Tavily", "LOCAL_TAVILY_API_KEY", "POST", TAVILY_API_URL,
                     lambda d: [Hit(x["url"], x.get("title") or "", x.get("content") or "", [TAVILY_API]) for x in (d.get("results") or [])[:10] if x.get("url")],
                     json={"query": query, "search_depth": "basic", "max_results": 10, "country": "france"},
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": USER_AGENT})


def serper_search(key: str, query: str):
    """API Serper (palier 2, PAYANT) : résultats Google en France."""
    return _api_call("API Serper", "LOCAL_SERPER_API_KEY", "POST", SERPER_API_URL,
                     lambda d: [Hit(x["link"], x.get("title") or "", x.get("snippet") or "", [SERPER_API]) for x in (d.get("organic") or [])[:10] if x.get("link")],
                     json={"q": query, "gl": "fr", "hl": "fr", "num": 10},
                     headers={"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": USER_AGENT})


def api_providers(brave_key: str | None = None, tavily_key: str | None = None, serper_key: str | None = None) -> list[Provider]:
    """Fournisseurs hors SearXNG CONFIGURÉS (clé présente et budget > 0), du moins cher au plus cher. None = clé de l'environnement."""
    brave_key = BRAVE_API_KEY if brave_key is None else brave_key
    tavily_key = TAVILY_API_KEY if tavily_key is None else tavily_key
    serper_key = SERPER_API_KEY if serper_key is None else serper_key
    out = []
    if brave_key and BRAVE_DAILY_BUDGET > 0:
        out.append(Provider(BRAVE_API, 1, BRAVE_DAILY_BUDGET, lambda q: brave_search(brave_key, q)))
    if tavily_key and TAVILY_DAILY_BUDGET > 0:
        out.append(Provider(TAVILY_API, 1, TAVILY_DAILY_BUDGET, lambda q: tavily_search(tavily_key, q), TAVILY_MONTHLY_BUDGET))
    if serper_key and SERPER_DAILY_BUDGET > 0:
        out.append(Provider(SERPER_API, 2, SERPER_DAILY_BUDGET, lambda q: serper_search(serper_key, q)))
    return out


def daily_budget_for(engine: str) -> int:
    return {BRAVE_API: BRAVE_DAILY_BUDGET, TAVILY_API: TAVILY_DAILY_BUDGET, SERPER_API: SERPER_DAILY_BUDGET}.get(engine, DAILY_BUDGET)


def provider_catalog(brave_key: str | None = None, tavily_key: str | None = None, serper_key: str | None = None) -> list[dict]:
    """Tous les fournisseurs HORS SearXNG, configurés ou non : palier, budgets, configuré et raison. Jamais la clé elle-même."""
    configured = {p.name for p in api_providers(brave_key, tavily_key, serper_key)}
    out = []
    for name, tier, daily, monthly, env in ((BRAVE_API, 1, BRAVE_DAILY_BUDGET, 0, "LOCAL_BRAVE_API_KEY"),
                                            (TAVILY_API, 1, TAVILY_DAILY_BUDGET, TAVILY_MONTHLY_BUDGET, "LOCAL_TAVILY_API_KEY"),
                                            (SERPER_API, 2, SERPER_DAILY_BUDGET, PAID_MONTHLY_BUDGET, "LOCAL_SERPER_API_KEY")):
        ok = name in configured
        note = None if ok else "budget à 0" if daily <= 0 else f"pas de clé ({env})"
        out.append({"engine": name, "tier": tier, "daily_budget": daily, "monthly_budget": max(0, monthly), "configured": int(ok), "config_note": note})
    return out


def declare_providers(conn, searx_names: list[str] | None = None, brave_key: str | None = None, tavily_key: str | None = None,
                      serper_key: str | None = None) -> None:
    """Upsert IDEMPOTENT d'une ligne par fournisseur dans local_engine_health (palier, budgets, configuré) pour l'écran Statistiques.

    Le web ne voit pas les variables LOCAL_* du worker : c'est le worker qui déclare. Ne touche JAMAIS aux compteurs, au cooldown ni à `enabled`
    (la cascade est inchangée) ; aucune clé n'est écrite. `searx_names` None : seuls les fournisseurs d'API sont (re)déclarés.
    """
    rows = [{"engine": n, "tier": 0, "daily_budget": DAILY_BUDGET, "monthly_budget": 0, "configured": 1, "config_note": None} for n in (searx_names or [])]
    rows += provider_catalog(brave_key, tavily_key, serper_key)
    try:
        for r in rows:
            db.execute(conn, """INSERT INTO local_engine_health (engine, tier, daily_budget, monthly_budget, configured, config_note, declared_at)
                                VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                                ON DUPLICATE KEY UPDATE tier=VALUES(tier), daily_budget=VALUES(daily_budget), monthly_budget=VALUES(monthly_budget),
                                                        configured=VALUES(configured), config_note=VALUES(config_note), declared_at=VALUES(declared_at)""",
                       (r["engine"], r["tier"], r["daily_budget"], r["monthly_budget"], r["configured"], r["config_note"]))
    except Exception as exc:  # noqa: BLE001 — migration 023 pas encore jouée : la recherche ne doit jamais en dépendre
        conn.rollback()
        log.warning("[local] déclaration des moteurs impossible (migration 023 appliquée ?) : %s", exc)
        for r in rows:
            if r["configured"]:
                db.execute(conn, "INSERT IGNORE INTO local_engine_health (engine) VALUES (%s)", (r["engine"],))
    conn.commit()


def next_available_at(conn, names: list[str] | None = None) -> str | None:
    """Heure (UTC, ISO) à laquelle un moteur redevient utilisable : fin de cooldown ou renouvellement du budget de 24 h. None si inconnue."""
    try:
        rows = db.fetch_all(conn, "SELECT * FROM local_engine_health WHERE enabled=1 AND COALESCE(configured, 1)=1")   # un fournisseur sans clé ne redevient jamais dispo
        now = db.fetch_one(conn, "SELECT UTC_TIMESTAMP() AS n")["n"]
    except Exception:  # noqa: BLE001 — simple indication pour l'écran
        return None
    import datetime as _dt
    times = []
    for r in rows:
        if names and r["engine"] not in names:
            continue
        t = now
        if r["cooldown_until"] is not None and r["cooldown_until"] > t:
            t = r["cooldown_until"]
        if r["window_start"] is not None and int(r["window_requests"] or 0) >= daily_budget_for(r["engine"]):
            t = max(t, r["window_start"] + _dt.timedelta(hours=24))
        times.append(t)
    return min(times).strftime("%Y-%m-%dT%H:%M:%SZ") if times else None


class SearchPool:
    """`pool(query) -> list[Hit]` (interface du résolveur de site) ; `pool.last` = détail du dernier appel.

    Cascade : palier 0 (SearXNG) → 1 (API gratuites) → 2 (payant). Un palier n'est interrogé que si AUCUN fournisseur du palier précédent n'a pu
    répondre ; avec LOCAL_PAID_ON_EMPTY=1, une réponse VIDE fait aussi passer aux paliers suivants. Le budget de 24 h (et mensuel) n'est jamais dépassé.
    """

    def __init__(self, conn, engines: str | None = None, url: str | None = None, api_key: str | None = None,
                 tavily_key: str | None = None, serper_key: str | None = None):
        self.conn, self.url = conn, url or SEARXNG_URL
        explicit = engines or os.getenv("LOCAL_SEARCH_ENGINES")
        found = [] if explicit else discover_enabled(self.url)
        searx_names = [e.strip() for e in (explicit or ",".join(found) or DEFAULT_ENGINES).split(",") if e.strip() and e.strip() not in API_PROVIDERS]
        self.providers: dict[str, Provider] = {n: Provider(n, 0, DAILY_BUDGET, (lambda q, _n=n: self._searx(_n, q))) for n in searx_names}
        for p in api_providers(api_key, tavily_key, serper_key):
            self.providers[p.name] = p                    # toujours après SearXNG : secours, du moins cher au plus cher
        self.names = list(self.providers)
        self.brave_key = BRAVE_API_KEY if api_key is None else api_key
        self.discovered = bool(found)
        self.last = Outcome([], [], [])
        self.foreign: list = []
        self._t_last: dict[str, float] = {}
        self.queries = self.empty = self.paid_queries = 0
        # une ligne par fournisseur (configuré ou non, avec palier et budgets) : l'écran Statistiques les montre tous, même à 0 requête
        declare_providers(conn, searx_names, api_key, tavily_key, serper_key)

    def tier(self, engine: str) -> int:
        p = self.providers.get(engine)
        return p.tier if p else 0

    # ── état ──────────────────────────────────────────────────────────────────────────────────────
    def rows(self) -> dict[str, dict]:
        return {r["engine"]: r for r in db.fetch_all(self.conn, "SELECT * FROM local_engine_health WHERE engine IN (%s)" % ",".join(["%s"] * len(self.names)), tuple(self.names))}

    def available(self) -> list[str]:
        """Fournisseurs utilisables (hors cooldown, dans leurs budgets), par palier ; SearXNG trié par santé, les API dans l'ordre de coût."""
        rows = self.rows()
        now = db.fetch_one(self.conn, "SELECT UTC_TIMESTAMP() AS n")["n"]
        month0 = now.date().replace(day=1)

        def month_used(r) -> int:
            ms = r.get("month_start")
            return int(r.get("month_requests") or 0) if ms is not None and ms >= month0 else 0
        paid_month = sum(month_used(r) for n, r in rows.items() if self.tier(n) == 2)

        def in_budget(n: str, r) -> bool:
            p = self.providers[n]
            fresh = r["window_start"] is not None and (now - r["window_start"]).total_seconds() < 24 * 3600
            if fresh and int(r["window_requests"] or 0) >= p.daily_budget:
                return False
            if p.monthly_budget > 0 and month_used(r) >= p.monthly_budget:
                return False
            return not (p.tier == 2 and PAID_MONTHLY_BUDGET > 0 and paid_month >= PAID_MONTHLY_BUDGET)
        ok = [n for n in self.names if rows.get(n) and rows[n]["enabled"] and (rows[n]["cooldown_until"] is None or rows[n]["cooldown_until"] <= now) and in_budget(n, rows[n])]
        order = {n: i for i, n in enumerate(self.names)}
        return sorted(ok, key=lambda n: (self.tier(n), -(rows[n]["health"] if rows[n]["health"] is not None else 60) if self.tier(n) == 0 else order[n]))

    def _record(self, engine: str, *, ok: bool, ms: int | None, results: int = 0, useful: int = 0, category: str | None = None, error: str | None = None) -> None:
        row = self.rows().get(engine) or {}
        req = int(row.get("requests") or 0) + 1
        avg = ms if row.get("avg_ms") is None or ms is None else int(0.8 * row["avg_ms"] + 0.2 * ms)
        cons = 0 if ok else int(row.get("consecutive_failures") or 0) + 1
        empty_streak = 0 if (not ok or results > 0) else int(row.get("consecutive_empty") or 0) + 1
        # Blocage silencieux : propre au scraping SearXNG (une API qui répond vide n'est pas bloquée, et un témoin y coûterait du quota ou de l'argent).
        silent_block = ok and self.tier(engine) == 0 and empty_streak >= EMPTY_STREAK_LIMIT
        canary = False
        if silent_block and self._canary_due(row):        # requête témoin (≤ 1 / jour) : si le moteur répond à une requête banale, ses « vides » étaient de vrais vides
            canary = True
            if self._canary_ok(engine):
                silent_block, empty_streak = False, 0
        new = {"requests": req, "successes": int(row.get("successes") or 0) + int(ok), "failures": int(row.get("failures") or 0) + int(not ok),
               "captchas": int(row.get("captchas") or 0) + int(category == errors.SEARCH_CAPTCHA),
               "timeouts": int(row.get("timeouts") or 0) + int(category == errors.SEARCH_TIMEOUT),
               "empty_results": int(row.get("empty_results") or 0) + int(ok and results == 0),
               "useful_results": int(row.get("useful_results") or 0), "consecutive_failures": cons, "avg_ms": avg}
        new["health"] = health_score(new)
        cooldown = None
        if silent_block:                                   # 0 résultat en série sans aucune erreur (et témoin déjà utilisé ou en échec) : traité comme un échec
            ok, category, error = False, errors.SEARCH_UNAVAILABLE, f"{empty_streak} réponses vides d'affilée (blocage silencieux probable)"
            cons = int(row.get("consecutive_failures") or 0) + 1
            new["failures"] += 1
            new["consecutive_failures"] = cons
            new["health"] = health_score(new)
        if not ok:
            # Exponentiel par échecs CONSÉCUTIFS (remis à 0 au premier succès) : CAPTCHA 180 → 360 → 720 → 1 440 min, timeout 15 → 30 → 60…
            minutes = min(COOLDOWN_MAX_MIN, COOLDOWN_BASE_MIN.get(category or errors.SEARCH_UNAVAILABLE, 30) * 2 ** min(10, cons - 1))
            cooldown = minutes
        db.execute(self.conn, """UPDATE local_engine_health SET requests=%s, successes=%s, failures=%s, captchas=%s, timeouts=%s, empty_results=%s,
                                 useful_results=%s, consecutive_failures=%s, avg_ms=%s, health=%s, consecutive_empty=%s,
                                 window_requests=IF(window_start IS NULL OR window_start < UTC_TIMESTAMP() - INTERVAL 24 HOUR, 1, window_requests + 1),
                                 window_start=IF(window_start IS NULL OR window_start < UTC_TIMESTAMP() - INTERVAL 24 HOUR, UTC_TIMESTAMP(), window_start),
                                 month_requests=IF(month_start IS NULL OR month_start < DATE(DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m-01')), 1, month_requests + 1),
                                 month_start=DATE(DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m-01')),
                                 last_canary_at=IF(%s, UTC_TIMESTAMP(), last_canary_at),
                                 last_success_at=IF(%s, UTC_TIMESTAMP(), last_success_at), last_failure_at=IF(%s, UTC_TIMESTAMP(), last_failure_at),
                                 last_error=IF(%s, %s, last_error), cooldown_until=IF(%s, UTC_TIMESTAMP() + INTERVAL %s MINUTE, NULL) WHERE engine=%s""",
                   (new["requests"], new["successes"], new["failures"], new["captchas"], new["timeouts"], new["empty_results"], new["useful_results"],
                    new["consecutive_failures"], new["avg_ms"], new["health"], 0 if silent_block else empty_streak, int(canary),
                    int(ok), int(not ok), int(not ok), (error or category or "")[:160], int(not ok), cooldown or 0, engine))
        self.conn.commit()
        if not ok:
            errors.record(self.conn, category or errors.SEARCH_UNAVAILABLE, engine, detail=error)
            log.warning("[local] moteur %s en cooldown %s min (%s)", engine, cooldown, category)

    def note_relevance(self, engines_with_relevant_hits: set[str]) -> None:
        """Appelé par le résolveur : quels moteurs ont rendu au moins un résultat PERTINENT (mot du nom, ville…) — la qualité, pas le volume."""
        for eng in engines_with_relevant_hits:
            if eng in self.names:
                db.execute(self.conn, "UPDATE local_engine_health SET useful_results = useful_results + 1 WHERE engine=%s", (eng,))
        self.conn.commit()

    def _canary_due(self, row: dict) -> bool:
        last = row.get("last_canary_at")
        if last is None:
            return True
        now = db.fetch_one(self.conn, "SELECT UTC_TIMESTAMP() AS n")["n"]
        return (now - last).total_seconds() >= CANARY_EVERY_H * 3600

    def _canary_ok(self, engine: str) -> bool:
        try:
            hits, cat, _msg, _ms = self._one(engine, CANARY_QUERY)
        except SearchUnavailable:
            return False
        return cat is None and len(hits) > 0

    # ── requête ───────────────────────────────────────────────────────────────────────────────────
    def _one(self, engine: str, query: str) -> tuple[list[Hit], str | None, str | None, int]:
        """(résultats, catégorie d'erreur, message, ms) du fournisseur. Une erreur du SERVEUR SearXNG lui-même lève SearchUnavailable."""
        p = self.providers.get(engine)
        return p.search(query) if p else self._searx(engine, query)

    def _searx(self, engine: str, query: str) -> tuple[list[Hit], str | None, str | None, int]:
        wait = self._t_last.get(engine, 0.0) + INTERVAL_S - time.monotonic()
        if wait > 0 and _transport is None:
            time.sleep(wait)
        self._t_last[engine] = time.monotonic()
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=25, transport=_transport) as c:
                r = c.get(f"{self.url}/search", params={"q": query, "format": "json", "language": "fr", "engines": engine}, headers={"User-Agent": USER_AGENT})
        except httpx.TimeoutException as exc:
            return [], errors.SEARCH_TIMEOUT, f"délai dépassé ({exc.__class__.__name__})", int((time.monotonic() - t0) * 1000)
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"SearXNG injoignable : {exc}") from exc
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 429:
            return [], errors.SEARCH_RATE_LIMIT, "SearXNG 429", ms
        if r.status_code >= 500:
            raise SearchUnavailable(f"SearXNG HTTP {r.status_code}")
        if r.status_code != 200:
            return [], errors.SEARCH_UNAVAILABLE, f"HTTP {r.status_code}", ms
        try:
            data = r.json()
        except ValueError:
            return [], errors.SEARCH_UNAVAILABLE, "réponse non JSON", ms
        # SearXNG liste aussi des moteurs NON demandés (autocomplétion, wikidata…) : seul le moteur interrogé nous concerne.
        bad = [(e[0], e[1]) for e in data.get("unresponsive_engines") or [] if len(e) >= 2 and str(e[0]).lower() == engine.lower()]
        results = data.get("results") or []
        if not results and bad:
            return [], classify_reason(str(bad[0][1])), f"{bad[0][0]} : {bad[0][1]}", ms
        hits = [Hit(x["url"], x.get("title") or "", x.get("content") or "", [str(e) for e in (x.get("engines") or [x.get("engine") or engine])]) for x in results[:10] if x.get("url")]
        served = {e for h in hits for e in h.engines}
        if hits and engine not in served and not any(e.split()[0] == engine for e in served):
            # Constaté sur le VPS : `engines=mojeek` ne rend AUCUN résultat de mojeek (moteur désactivé sur l'instance → SearXNG répond avec d'autres moteurs).
            # Les résultats restent valables, mais le moteur demandé ne sert rien : il est écarté, pas crédité de résultats qui ne sont pas les siens.
            self.foreign = hits
            return hits, "ENGINE_NOT_SERVED", f"{engine} ne fournit aucun résultat (désactivé sur l'instance ? résultats de : {', '.join(sorted(served))})", ms
        return hits, None, None, ms

    def __call__(self, query: str) -> list[Hit]:
        avail = self.available()
        if not avail:
            self.last = Outcome([], [], [])
            raise SearchUnavailable("tous les moteurs sont en cooldown ou ont atteint leur budget de 24 h : " + ", ".join(self.names))
        by_tier: dict[int, list[str]] = {}
        for n in avail:
            by_tier.setdefault(self.tier(n), []).append(n)
        merged: dict[str, Hit] = {}
        ok_engines: list[str] = []
        failed: list[tuple[str, str]] = []
        paid = False
        for tier in sorted(by_tier):
            if ok_engines and (merged or not PAID_ON_EMPTY):
                break                                     # un palier a répondu : on n'entame pas le quota (ni l'argent) du suivant
            higher = any(t > tier for t in by_tier)
            names = by_tier[tier][:QUERY_ENGINES + 2] if tier == 0 else by_tier[tier]   # jamais plus de 4 moteurs SearXNG pour une requête
            answered = 0
            for eng in names:
                if answered and (tier > 0 or merged or answered >= QUERY_ENGINES):
                    break                                 # SearXNG : le 2e moteur seulement si le 1er n'a RIEN rendu ; une API qui répond suffit
                if tier > 0 and eng not in self.available():
                    continue                              # re-vérification juste avant l'appel : le budget n'est jamais dépassé
                self.foreign = []
                try:
                    hits, cat, msg, ms = self._one(eng, query)
                except SearchUnavailable as exc:          # SearXNG lui-même en panne : on passe directement au palier suivant s'il existe
                    if not higher and not ok_engines:
                        raise
                    failed.append((eng, errors.SEARCH_UNAVAILABLE))
                    log.warning("[local] %s — passage au palier suivant", exc)
                    break
                if cat == "ENGINE_NOT_SERVED":
                    failed.append((eng, errors.SEARCH_UNAVAILABLE))
                    self._record(eng, ok=False, ms=ms, category=errors.SEARCH_UNAVAILABLE, error=msg)
                    ok_engines.append(f"(via {', '.join(sorted({e for h in hits for e in h.engines}))})")
                    answered += 1
                    for h in hits:
                        merged.setdefault(h.url, h)
                    continue
                if cat:
                    failed.append((eng, cat))
                    self._record(eng, ok=False, ms=ms, category=cat, error=msg)
                    continue
                ok_engines.append(eng)
                answered += 1
                paid = paid or tier >= 2
                self._record(eng, ok=True, ms=ms, results=len(hits), useful=len([h for h in hits if h.domain]))
                for h in hits:
                    if h.url in merged:
                        merged[h.url].engines = sorted(set(merged[h.url].engines + [eng]))
                    else:
                        merged[h.url] = h
        self.queries += 1
        self.paid_queries += int(paid)
        self.last = Outcome(list(merged.values()), ok_engines, failed)
        if not ok_engines:
            raise SearchUnavailable("aucun moteur n'a répondu : " + ", ".join(f"{e} ({c})" for e, c in failed))
        if not merged:
            self.empty += 1
        return list(merged.values())


def describe_tiers(searx_names: list[str] | None = None) -> list[str]:
    """Lignes lisibles : paliers, fournisseurs configurés et budgets (aucun appel réseau)."""
    lines = [f"palier 0 — {TIER_LABEL[0]} : {', '.join(searx_names or []) or '(aucun moteur découvert)'} — {DAILY_BUDGET} req / 24 h par moteur, "
             f"2e moteur seulement si le 1er rend 0 résultat, témoin ≤ 1 / {CANARY_EVERY_H} h"]
    configured = {p.name: p for p in api_providers()}
    for name, tier, daily, monthly, env in ((BRAVE_API, 1, BRAVE_DAILY_BUDGET, 0, "LOCAL_BRAVE_API_KEY"),
                                            (TAVILY_API, 1, TAVILY_DAILY_BUDGET, TAVILY_MONTHLY_BUDGET, "LOCAL_TAVILY_API_KEY"),
                                            (SERPER_API, 2, SERPER_DAILY_BUDGET, PAID_MONTHLY_BUDGET, "LOCAL_SERPER_API_KEY")):
        state = "ACTIF" if name in configured else f"inactif ({env} absente)" if daily > 0 else "désactivé (budget 0)"
        month = f", {monthly} / mois" if monthly > 0 else ""
        lines.append(f"palier {tier} — {TIER_LABEL[tier]} : {name:10s} {state} — {daily} req / 24 h{month}")
    lines.append(f"payant si le gratuit répond VIDE (LOCAL_PAID_ON_EMPTY) : {'oui' if PAID_ON_EMPTY else 'non'}")
    return lines


def main() -> int:
    """`python -m worker.local.engines` : paliers et budgets, moteurs actifs de l'instance et ce que rend réellement chaque moteur demandé (aucune écriture)."""
    names = discover_enabled(SEARXNG_URL)
    print("Paliers de recherche (gratuit d'abord, payant en dernier recours) :")
    for line in describe_tiers(names):
        print("  " + line)
    try:                                                   # consommation du jour / du mois, si la base est joignable (lecture seule)
        with db.connect() as conn:
            for r in db.fetch_all(conn, "SELECT engine, window_requests, window_start, month_requests, cooldown_until FROM local_engine_health ORDER BY engine"):
                print(f"  usage {r['engine']:14s} 24 h : {r['window_requests']} (depuis {r['window_start']}) · mois : {r.get('month_requests')} · cooldown : {r['cooldown_until'] or '-'}")
    except Exception as exc:  # noqa: BLE001 — affichage seulement
        print(f"  (usage non lisible : base injoignable — {type(exc).__name__})")
    print("moteurs « general » actifs sur", SEARXNG_URL, ":", names or "(illisible : /config injoignable)")
    for eng in names[:8]:
        try:
            with httpx.Client(timeout=40) as c:
                d = c.get(f"{SEARXNG_URL}/search", params={"q": '"boulangerie" "Troyes"', "format": "json", "engines": eng, "language": "fr"}, headers={"User-Agent": USER_AGENT}).json()
            served: dict[str, int] = {}
            for r in d.get("results", []):
                for e in r.get("engines") or []:
                    served[e] = served.get(e, 0) + 1
            print(f"  {eng:14s} {len(d.get('results', [])):3d} résultats, servis par {served} {'⚠ ne sert pas ce moteur' if d.get('results') and eng not in served else ''}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {eng:14s} ERREUR {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
