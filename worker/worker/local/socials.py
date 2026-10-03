"""Réseaux sociaux et professionnels VÉRIFIÉS d'une entreprise (Facebook, Instagram, LinkedIn…) — jamais une page devinée ou voisine.

Deux sources, jamais d'ouverture de la page du réseau (murs de connexion : aucun contournement) :
  * les liens publiés sur le SITE OFFICIEL de l'entreprise (confiance 0,9) : c'est elle qui les revendique ;
  * les résultats de recherche (titre + extrait + adresse) : retenus seulement si le nom distinctif de l'entreprise ET sa ville (ou son code postal)
    y figurent, et si l'adresse est un PROFIL (pas une publication, un groupe, un partage, une recherche). Confiance 0,7.
Un profil LinkedIn personnel (/in/…) n'est retenu que s'il porte le nom du dirigeant connu.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .textmatch import distinctive_tokens, fold, name_tokens, token_fraction

NETWORKS = {"facebook.com": "Facebook", "instagram.com": "Instagram", "linkedin.com": "LinkedIn", "tiktok.com": "TikTok",
            "youtube.com": "YouTube", "pinterest.com": "Pinterest", "pinterest.fr": "Pinterest", "x.com": "X", "twitter.com": "X"}
_NOT_PROFILE = re.compile(r"^/(?:sharer|share|intent|dialog|plugins|groups|events|posts|photos|photo|videos|video|watch|hashtag|explore|p|reel|reels|"
                          r"stories|search|pulse|jobs|login|signup|tr|help|policies|legal|privacy|about|home|marketplace|gaming|watch|embed|"
                          r"feed|results|shorts|hashtags|l\.php|pages/category|public)(?:[/.?]|$)", re.I)
SITE_CONF, SEARCH_CONF = 0.9, 0.7
MIN_NAME_MATCH = 0.75


def network_of(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("fr.").removeprefix("business.")
    return next((n for d, n in NETWORKS.items() if host == d or host.endswith("." + d)), None)


def normalize(url: str) -> str | None:
    """Adresse canonique d'un PROFIL (sans paramètres de suivi), ou None si ce n'est pas un profil (publication, partage, recherche…)."""
    net = network_of(url)
    if not net:
        return None
    u = urlparse(url if "://" in url else "https://" + url)
    path = re.sub(r"/+", "/", u.path or "/")
    if net == "Facebook" and path.rstrip("/") == "/profile.php":
        pid = (parse_qs(u.query).get("id") or [""])[0]
        return f"https://www.facebook.com/profile.php?id={pid}" if pid.isdigit() else None
    if path in ("", "/") or _NOT_PROFILE.search(path):
        return None
    parts = [x for x in path.split("/") if x]
    if net == "LinkedIn":
        if len(parts) < 2 or parts[0] not in ("company", "in", "school"):
            return None
        parts = parts[:2]
    elif net == "YouTube":
        parts = parts[:2] if parts[0] in ("channel", "c", "user") else parts[:1]
        if not parts or (parts[0] in ("channel", "c", "user") and len(parts) < 2):
            return None
    elif net == "Facebook" and parts[0] == "pages":
        parts = parts[:3]                                          # /pages/Nom/123456
    else:
        parts = parts[:1]
    host = {"Facebook": "www.facebook.com", "Instagram": "www.instagram.com", "LinkedIn": "www.linkedin.com", "TikTok": "www.tiktok.com",
            "YouTube": "www.youtube.com", "Pinterest": "www.pinterest.fr", "X": "x.com"}[net]
    return f"https://{host}/" + "/".join(parts)


def _link(url: str, via: str, conf: float) -> dict:
    return {"network": network_of(url), "url": url, "via": via, "confidence": conf}


def from_pages(pages) -> list[dict]:
    """Profils liés depuis le site officiel (en-tête, pied de page…)."""
    out = []
    for page in pages or []:
        for href, _text in getattr(page, "links", []):
            if (n := normalize(href)):
                out.append(_link(n, "site", SITE_CONF))
    return merge(out)


def from_hits(company: dict, hits) -> list[dict]:
    """Profils trouvés par la recherche, seulement s'ils nomment l'entreprise ET sa localité."""
    names = [n for n in (company.get("trade_name"), company.get("company_name")) if n]
    city, cp = fold(company.get("city")), (company.get("postal_code") or "").strip()
    manager = name_tokens(company.get("manager_name"))
    out = []
    for h in hits or []:
        url = getattr(h, "url", "") or ""
        n = normalize(url)
        if not n:
            continue
        text = " ".join([getattr(h, "title", "") or "", getattr(h, "content", "") or "", urlparse(url).path.replace("-", " ").replace(".", " ")])
        if "/in/" in n:
            if len(manager) >= 2 and token_fraction(manager, text) == 1.0 and (city and f" {city} " in f" {fold(text)} "):
                out.append(_link(n, "search", SEARCH_CONF))
            continue
        named = any(token_fraction(distinctive_tokens(x) or name_tokens(x), text) >= MIN_NAME_MATCH for x in names)
        local = bool(city and f" {city} " in f" {fold(text)} ") or bool(cp and cp in text)
        if named and local:
            out.append(_link(n, "search", SEARCH_CONF))
    return merge(out)


def merge(*lists) -> list[dict]:
    """Un profil par réseau (le plus fiable ; le site officiel l'emporte), sans doublon."""
    best: dict[str, dict] = {}
    for lst in lists:
        for x in lst or []:
            if not x.get("network") or not x.get("url"):
                continue
            cur = best.get(x["network"])
            if cur is None or x["confidence"] > cur["confidence"]:
                best[x["network"]] = x
    order = ["Facebook", "Instagram", "LinkedIn", "TikTok", "YouTube", "Pinterest", "X"]
    return sorted(best.values(), key=lambda x: order.index(x["network"]) if x["network"] in order else 99)
