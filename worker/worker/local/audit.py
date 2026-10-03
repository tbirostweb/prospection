"""Audit LÉGER, PASSIF et poli d'un site déjà identifié avec confiance (≥ 0,80).

Au plus 4 requêtes GET ordinaires (accueil, variante http, robots.txt, sitemap) — ce que ferait un visiteur ou un moteur de recherche.
Jamais : recherche de vulnérabilités, scan de ports, tests de mots de passe, injections, exploits, contournement de protection.

Les métriques restent SÉPARÉES (un site peut être lent mais bien référencé, ou rapide mais mal référencé) :
  seo_score (0-100, plus haut = mieux)     bases SEO présentes : title, description, H1, canonical, indexabilité, sitemap, données structurées…
  technical_score                          HTTPS, redirection http→https, viewport (mobile), favicon, encodage, balises obsolètes
  performance_score                        LÉGER : temps de réponse HTML et poids de la page — ce n'est PAS Lighthouse (voir pagespeed.py)
  seo_opportunity_score = 100 − seo_score
  modernization                            LOW / MEDIUM / HIGH d'après des indices OBJECTIFS (jamais l'apparence, jamais Ollama)
Aucun test isolé ne dit « ce site n'a aucun référencement » : on écrit « bases SEO manquantes », « SEO technique améliorable »,
« signaux SEO faibles » ou « aucun problème majeur détecté ».
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from . import htmlinfo
from .signatures import CMS_SIGNATURES, ECOM_SIGNATURES, SITE_BUILDERS

GENERIC_TITLES = {"accueil", "home", "index", "bienvenue", "welcome", "untitled", "sans titre", "page d accueil", "nouveau site", "mon site", "site web"}
OLD_TECH = [
    (3, "balise <marquee> / <blink> (obsolète)", r"<\s*(marquee|blink)\b"),
    (3, "frames HTML (<frameset>, <frame>)", r"<\s*(frameset|frame)\b"),
    (3, "Flash / SWF", r"\.swf\b|swfobject|shockwave"),
    (2, "balises <font> / <center> (mise en forme HTML ancienne)", r"<\s*(font|center)\b"),
    (2, "commentaires conditionnels Internet Explorer", r"<!--\[if\s+(?:lt|lte|gt)?\s*ie"),
    (2, "jQuery 1.x", r"jquery[-./]?(?:min[-.])?1\.\d+\.\d+|jquery/1\.\d"),
    (1, "doctype HTML 4 / XHTML 1.0", r"<!doctype html public|xhtml 1\.0"),
]
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}
# Site sur un sous-domaine gratuit : peu crédible, pub de l'hébergeur, mal référencé. Site d'annuaire : souvent un abonnement mensuel cher et générique.
FREE_SUBDOMAINS = ("wixsite.com", "jimdosite.com", "jimdofree.com", "jimdo.com", "e-monsite.com", "webself.net", "wordpress.com", "over-blog.com",
                   "blogspot.com", "sitew.fr", "sitew.com", "business.site", "godaddysites.com", "site123.me", "weebly.com", "strikingly.com",
                   "mystrikingly.com", "simplesite.com", "webnode.fr", "webnode.com", "wifeo.com", "free.fr", "pagesperso-orange.fr", "canva.site",
                   "carrd.co", "square.site", "editorx.io")
DIRECTORY_SITE = re.compile(r"\bsolocal\.com|site-solocal\.com|r[ée]alis[ée] par (?:solocal|pagesjaunes)", re.I)


@dataclass
class Issue:
    code: str
    label: str
    severity: str            # info | low | medium | high
    category: str            # seo | technical | performance | mobile | security | availability
    evidence: str = ""


@dataclass
class Audit:
    reachable: bool = False
    status: int | None = None
    final_url: str | None = None
    https: bool | None = None
    http_redirects_to_https: bool | None = None
    response_ms: int | None = None
    html_bytes: int | None = None
    title: str = ""
    meta_description: bool = False
    h1_count: int = 0
    canonical: bool = False
    noindex: bool = False
    viewport: bool = False
    opengraph: bool = False
    favicon: bool = False
    structured_data: list[str] = field(default_factory=list)
    lang: str | None = None
    robots_txt: str = "unknown"          # ok | missing | blocks_all | unknown
    sitemap: bool | None = None
    cms: str | None = None
    ecommerce: bool = False
    old_signals: list[str] = field(default_factory=list)
    copyright_year: int | None = None
    issues: list[Issue] = field(default_factory=list)
    technical_score: int | None = None
    seo_score: int | None = None
    performance_score: int | None = None
    seo_opportunity_score: int | None = None
    modernization: str = "LOW"
    modernization_evidence: list[str] = field(default_factory=list)
    # Performance LÉGÈRE (sans Lighthouse) : temps de récupération de l'accueil, poids, compression, cache, nombre de ressources
    performance_status: str = "UNKNOWN"       # LIGHT (mesure légère) | UNKNOWN (rien mesuré) ; Lighthouse : optionnel, jamais requis
    compression: str | None = None
    cache_control: str | None = None
    resources: dict = field(default_factory=dict)        # {"scripts": n, "stylesheets": n, "images": n}
    seo_evidence: list[dict] = field(default_factory=list)          # [{"check", "ok", "detail"}] : preuves, jamais un verdict « mauvais référencement »
    seo_signal: str | None = None              # SEO_TECHNICAL_OPPORTUNITY quand des bases techniques manquent
    free_subdomain: str | None = None          # « monsalon.wixsite.com » → « wixsite.com »
    directory_site: bool = False               # site fourni par un annuaire (Solocal / PagesJaunes)
    js_shell: bool = False                     # page quasi vide sans JavaScript (rendu côté client) ou mur anti-bot : SEO NON ÉVALUABLE de façon fiable
    local_presence: dict = field(default_factory=dict)  # signaux de présence locale (LocalBusiness, adresse, ville dans le title) — PAS un classement Google

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "issues"}
        d["issues"] = [i.__dict__ for i in self.issues]
        return d


def _response_score(ms: int | None, size: int | None) -> int | None:
    if ms is None:
        return None
    base = 100 if ms <= 300 else 90 if ms <= 800 else 75 if ms <= 1500 else 50 if ms <= 3000 else 30 if ms <= 5000 else 10
    return max(0, base - (15 if (size or 0) > 500_000 else 0))


def seo_summary(audit: dict | Audit) -> str:
    s = audit.get("seo_score") if isinstance(audit, dict) else audit.seo_score
    if s is None:
        return "SEO non évalué"
    return "bases SEO manquantes" if s < 50 else "SEO technique améliorable" if s < 75 else "aucun problème majeur détecté"


def _origin(url: str, scheme: str | None = None) -> str:
    p = urlparse(url)
    return f"{scheme or p.scheme}://{p.netloc}/"


def audit_site(url: str, fetch, home: htmlinfo.Page | None = None, now: date | None = None, clock=time.monotonic) -> Audit:
    """Audite le site officiel `url`. `fetch(url) -> Fetched | None` (net.try_get en production). `home` : page d'accueil déjà lue (évite un GET)."""
    a = Audit()
    now = now or date.today()
    t0 = clock()
    got = fetch(_origin(url)) if home is None else None
    a.response_ms = int((clock() - t0) * 1000) if got is not None else None
    if home is None:
        if got is None:
            a.issues.append(Issue("unreachable", "Site inaccessible (pas de réponse)", "high", "availability", "aucune réponse à la requête d'accueil"))
            a.technical_score = 0
            return a
        a.status, a.final_url = got.status, got.url
        a.compression = (getattr(got, "headers", {}) or {}).get("content-encoding") or None
        a.cache_control = (getattr(got, "headers", {}) or {}).get("cache-control") or None
        if not got.ok:
            a.issues.append(Issue("http_error", f"Site en erreur (HTTP {got.status})", "high", "availability", f"HTTP {got.status}"))
            a.technical_score = 0
            return a
        home = htmlinfo.parse(got.text, got.url)
        a.html_bytes = len(got.text.encode("utf-8", "ignore"))
    else:
        a.status, a.final_url, a.html_bytes = 200, home.url, len(home.raw)
    a.reachable = True
    a.https = urlparse(a.final_url).scheme == "https"

    # http → https (une requête de plus, seulement si l'accueil a été lu en https)
    if a.https:
        plain = fetch(_origin(a.final_url, "http"))
        a.http_redirects_to_https = None if plain is None else urlparse(plain.url).scheme == "https"
    # robots.txt / sitemap
    robots = fetch(_origin(a.final_url) + "robots.txt")
    sitemap_url = None
    if robots is None:
        a.robots_txt = "unknown"
    elif robots.ok and robots.text and "<html" not in robots.text[:300].lower():
        blocks = re.search(r"(?im)^user-agent:\s*\*\s*$(?:\n(?!user-agent).*)*?^disallow:\s*/\s*$", robots.text)
        a.robots_txt = "blocks_all" if blocks else "ok"
        if m := re.search(r"(?im)^sitemap:\s*(\S+)", robots.text):
            sitemap_url = m.group(1)
    else:
        a.robots_txt = "missing"
    sm = fetch(sitemap_url or _origin(a.final_url) + "sitemap.xml")
    a.sitemap = None if sm is None else bool(sm.ok and sm.text and re.search(r"<(urlset|sitemapindex)\b", sm.text[:2000]))

    # HTML
    a.title = home.title
    a.meta_description = bool((home.meta.get("description") or "").strip())
    a.h1_count = len([h for h in home.h1 if h])
    a.canonical = bool(home.canonical)
    robots_meta = (home.meta.get("robots") or "").lower()
    a.noindex = "noindex" in robots_meta
    a.viewport = "width" in (home.meta.get("viewport") or "").lower()
    a.opengraph = any(k.startswith("og:") for k in home.meta)
    a.favicon = home.has_favicon
    a.structured_data = sorted({str(t) for i in home.jsonld for t in ([i.get("@type")] if isinstance(i.get("@type"), str) else (i.get("@type") or []))})
    a.lang = home.lang
    raw = home.raw
    a.resources = {"scripts": len(re.findall(r"<script[^>]+src=", raw)), "stylesheets": len(re.findall(r"<link[^>]+rel=[\"']?stylesheet", raw)),
                   "images": len(re.findall(r"<img\b", raw))}
    visible = len(home.text)
    bot_wall = bool(re.search(r"just a moment|attention required|access denied|verify you are human|captcha|cf-chl|datadome|px-captcha|incapsula|challenge-platform", raw[:20000])) and visible < 600
    a.js_shell = visible < 250 and (a.resources["scripts"] >= 2 or bot_wall or (a.html_bytes or 0) > 5000) or bot_wall
    a.cms = next((name for name, rx in CMS_SIGNATURES if re.search(rx, raw)), None)
    a.ecommerce = bool(re.search(ECOM_SIGNATURES, raw))
    a.old_signals = [label for _, label, rx in OLD_TECH if re.search(rx, raw)]
    if m := re.search(r"WordPress\s+([1-4])\.\d", home.meta.get("generator", "")):
        a.old_signals.append(f"WordPress {m.group(0).split()[-1]} (version très ancienne)")
    host = (urlparse(a.final_url).hostname or "").lower()
    a.free_subdomain = next((d for d in FREE_SUBDOMAINS if host == d or host.endswith("." + d)), None)
    a.directory_site = bool(DIRECTORY_SITE.search(host) or DIRECTORY_SITE.search(raw[-30000:]) or DIRECTORY_SITE.search(raw[:5000]))
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d{2}|19\d{2})", raw)]
    a.copyright_year = max(years) if years else None

    _issues_and_scores(a, now)
    return a


def _issues_and_scores(a: Audit, now: date) -> None:
    add = lambda code, label, sev, cat, ev="": a.issues.append(Issue(code, label, sev, cat, ev))       # noqa: E731
    if a.js_shell:
        # Constaté en réel (site marchand FR) : 14 Ko de HTML, 0 caractère visible → « H1 absent, pas de meta description » aurait été FAUX pour Google, qui exécute le JS.
        add("js_shell", "Contenu chargé en JavaScript ou protégé par un anti-bot : SEO non évaluable de façon fiable", "info", "seo",
            f"{len(a.title)} car. de titre, aucun texte visible sans JavaScript")
    seo, tech = 100, 100
    t = a.title.strip()
    if not t:
        add("title_missing", "Balise title absente", "high", "seo"); seo -= 25
    elif t.lower().strip(" -|") in GENERIC_TITLES or len(t) < 8:
        add("title_generic", "Title très générique", "medium", "seo", f"« {t} »"); seo -= 10
    if not a.meta_description and not a.js_shell:
        add("meta_description_missing", "Meta description absente", "medium", "seo"); seo -= 20
    if a.h1_count == 0 and not a.js_shell:
        add("h1_missing", "Aucun titre H1", "medium", "seo"); seo -= 15
    elif a.h1_count > 1:
        add("h1_multiple", f"{a.h1_count} titres H1 sur l'accueil", "low", "seo"); seo -= 3
    if not a.canonical:
        add("canonical_missing", "Balise canonical absente", "low", "seo"); seo -= 5
    if a.noindex:
        add("noindex", "Page d'accueil non indexable (noindex)", "high", "seo", "meta robots : noindex"); seo -= 40
    if a.robots_txt == "blocks_all":
        add("robots_blocks_all", "robots.txt interdit tout le site aux moteurs", "high", "seo"); seo -= 40
    if a.sitemap is False:
        add("sitemap_missing", "Sitemap XML introuvable", "low", "seo"); seo -= 10
    if not a.structured_data and not a.js_shell:
        add("structured_data_missing", "Aucune donnée structurée (schema.org)", "low", "seo"); seo -= 8
    if not a.opengraph and not a.js_shell:
        add("opengraph_missing", "Balises OpenGraph absentes", "info", "seo"); seo -= 4
    if not a.lang:
        add("lang_missing", "Langue de la page non déclarée", "info", "seo"); seo -= 3

    if a.https is False:
        add("no_https", "Site en HTTP non sécurisé (pas de HTTPS)", "high", "security"); tech -= 30
    elif a.http_redirects_to_https is False:
        add("no_https_redirect", "HTTP ne redirige pas vers HTTPS", "medium", "security"); tech -= 10
    if not a.viewport:
        add("no_viewport", "Pas de balise viewport : affichage mobile probablement non adapté", "high", "mobile"); tech -= 30
    if a.free_subdomain:
        add("free_subdomain", f"Site sur un sous-domaine gratuit ({a.free_subdomain}) : peu crédible et mal référencé", "medium", "technical", a.free_subdomain)
    if a.cms in SITE_BUILDERS and not a.free_subdomain:
        add("site_builder", f"Site fait avec un créateur de sites ({a.cms}) : souvent peu personnalisé et moins bien référencé", "low", "technical", a.cms)
    if a.directory_site:
        add("directory_site", "Site fourni par un annuaire (Solocal / PagesJaunes) : souvent un abonnement coûteux et générique", "medium", "technical")
    if not a.favicon:
        add("favicon_missing", "Favicon absente", "info", "technical"); tech -= 3
    for label in a.old_signals:
        add("old_tech", f"Technologie ancienne : {label}", "medium", "technical", label); tech -= 10
    if a.response_ms is not None and a.response_ms > 3000:
        add("slow_response", f"Réponse lente ({a.response_ms / 1000:.1f} s pour l'accueil)", "medium", "performance", f"{a.response_ms} ms")
    if (a.html_bytes or 0) > 500_000:
        add("heavy_page", "Page d'accueil très lourde (HTML > 500 Ko)", "low", "performance")

    total_res = sum(a.resources.values())
    if total_res > 80:
        add("many_resources", f"Beaucoup de ressources sur l'accueil (≈ {total_res} scripts/feuilles de style/images)", "low", "performance", str(a.resources))
    if a.compression is None and (a.html_bytes or 0) > 30_000 and a.response_ms is not None:
        add("no_compression", "Réponse HTML non compressée (gzip / br absents)", "low", "performance")
    a.seo_evidence = [
        {"check": "title", "ok": bool(t) and t.lower().strip(" -|") not in GENERIC_TITLES and len(t) >= 8, "detail": f"« {t[:80]} »" if t else "absent"},
        {"check": "meta_description", "ok": a.meta_description, "detail": "présente" if a.meta_description else "absente"},
        {"check": "h1", "ok": a.h1_count >= 1, "detail": f"{a.h1_count} titre(s) H1"},
        {"check": "canonical", "ok": a.canonical, "detail": "déclarée" if a.canonical else "absente"},
        {"check": "indexable", "ok": not a.noindex and a.robots_txt != "blocks_all", "detail": f"robots.txt : {a.robots_txt} ; meta robots : {'noindex' if a.noindex else 'ok'}"},
        {"check": "sitemap", "ok": bool(a.sitemap), "detail": {True: "trouvé", False: "introuvable", None: "non vérifié"}[a.sitemap]},
        {"check": "structured_data", "ok": bool(a.structured_data), "detail": ", ".join(a.structured_data[:4]) or "aucune"},
        {"check": "viewport", "ok": a.viewport, "detail": "déclaré" if a.viewport else "absent"},
    ]
    a.seo_signal = "SEO_TECHNICAL_OPPORTUNITY" if seo < 75 else None
    a.performance_status = "LIGHT" if a.response_ms is not None else "UNKNOWN"
    a.seo_score, a.technical_score = max(0, min(100, seo)), max(0, min(100, tech))
    a.seo_opportunity_score = 100 - a.seo_score
    if a.js_shell:                                     # inconnu ≠ mauvais : pas de score SEO, pas de « opportunité SEO » inventée
        a.seo_score = a.seo_opportunity_score = None
        a.seo_signal = None
        for e in a.seo_evidence:
            if e["check"] in ("meta_description", "h1", "structured_data", "title"):
                e["ok"], e["detail"] = None, "non évaluable (page rendue en JavaScript ou protégée)"
    a.performance_score = _response_score(a.response_ms, a.html_bytes)

    pts, ev = 0, []
    if a.https is False:
        pts += 3; ev.append("site en HTTP non sécurisé")
    if not a.viewport:
        pts += 3; ev.append("pas de balise viewport (mobile)")
    for label in a.old_signals:
        pts += 3 if any(w in label for w in ("Flash", "frames", "marquee", "WordPress")) else 2
        ev.append(label)
    if a.free_subdomain:
        pts += 2; ev.append(f"sous-domaine gratuit ({a.free_subdomain})")
    if a.directory_site:
        pts += 1; ev.append("site fourni par un annuaire (Solocal / PagesJaunes)")
    if a.cms in SITE_BUILDERS and not a.free_subdomain:
        pts += 1; ev.append(f"site fait avec un créateur de sites ({a.cms})")
    if a.copyright_year and a.copyright_year <= now.year - 6:
        pts += 1; ev.append(f"mention © {a.copyright_year} (signal FAIBLE, jamais suffisant seul)")
    local_types = {t.lower() for t in a.structured_data}
    city_in_title = False
    a.local_presence = {"local_business_schema": any(t in local_types for t in ("localbusiness", "restaurant", "store", "hotel", "foodestablishment", "autorepair", "beautysalon", "hairsalon", "bakery", "plumber", "electrician")),
                        "title_has_locality": None, "contact_page_found": None, "note": "signaux de présence locale seulement : aucun classement Google n'est mesuré"}
    a.modernization = "HIGH" if pts >= 5 else "MEDIUM" if pts >= 3 else "LOW"
    a.modernization_evidence = ev
