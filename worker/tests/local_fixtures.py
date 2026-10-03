"""Fixtures de la prospection locale : entreprises, pages de sites et faux réseau (aucun accès Internet)."""
from __future__ import annotations

from datetime import date

from worker.net import Fetched

MARTIN = dict(  # boulangerie indépendante de Troyes : cas « site officiel correctement trouvé »
    siret="12345678900011", siren="123456789", company_name="MARTIN BOULANGERIE SARL", trade_name="Boulangerie Martin", city="TROYES",
    postal_code="10000", address="12 RUE EMILE ZOLA 10000 TROYES", phone=None)


def html(title="Boulangerie Martin - Troyes", h1="Boulangerie Martin", desc="Pains et viennoiseries à Troyes", viewport=True, canonical=True,
         favicon=True, jsonld=None, og=True, lang="fr", body="", head_extra="", noindex=False, doctype="<!DOCTYPE html>") -> str:
    meta = ""
    if desc:
        meta += f'<meta name="description" content="{desc}">'
    if viewport:
        meta += '<meta name="viewport" content="width=device-width, initial-scale=1">'
    if noindex:
        meta += '<meta name="robots" content="noindex,nofollow">'
    if og:
        meta += '<meta property="og:title" content="x">'
    if canonical:
        meta += '<link rel="canonical" href="https://www.boulangerie-martin.fr/">'
    if favicon:
        meta += '<link rel="icon" href="/favicon.ico">'
    ld = f'<script type="application/ld+json">{jsonld}</script>' if jsonld else ""
    h = f"<h1>{h1}</h1>" if h1 else ""
    t = f"<title>{title}</title>" if title is not None else ""
    return f'{doctype}<html lang="{lang}"><head>{t}{meta}{ld}{head_extra}</head><body>{h}{body}<footer>© 2026</footer></body></html>'


ROBOTS_OK = "User-agent: *\nDisallow: /admin"
SITEMAP = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.boulangerie-martin.fr/</loc></url></urlset>'
LEGAL_MARTIN = html(title="Mentions légales - Boulangerie Martin", h1="Mentions légales",
                    body="<p>MARTIN BOULANGERIE SARL - SIRET 123 456 789 00011 - 12 rue Emile Zola 10000 Troyes - 03 25 12 34 56 - contact@boulangerie-martin.fr</p>")
GOOD_JSONLD = '{"@context":"https://schema.org","@type":"Bakery","name":"Boulangerie Martin","address":{"addressLocality":"Troyes","postalCode":"10000"}}'


class FakeWeb:
    """`fetch(url)` : sert des pages prédéfinies (status, corps). URL absente = site injoignable (None). Enregistre les URL demandées."""

    def __init__(self, pages: dict[str, tuple[int, str]] | None = None, redirects: dict[str, str] | None = None):
        self.pages = pages or {}
        self.redirects = redirects or {}
        self.calls: list[str] = []
        self.elapsed_ms: int | None = 120
        self.headers: dict = {"content-encoding": "gzip", "cache-control": "max-age=3600"}

    def add_site(self, domain: str, home: str, legal: str | None = None, robots: str | None = ROBOTS_OK, sitemap: str | None = SITEMAP,
                 https: bool = True, redirect_http: bool = True):
        scheme = "https" if https else "http"
        self.pages[f"{scheme}://{domain}/"] = (200, home)
        if legal:
            self.pages[f"{scheme}://{domain}/mentions-legales"] = (200, legal)
        if robots is not None:
            self.pages[f"{scheme}://{domain}/robots.txt"] = (200, robots)
        if sitemap is not None:
            self.pages[f"{scheme}://{domain}/sitemap.xml"] = (200, sitemap)
        if https and redirect_http:
            self.redirects[f"http://{domain}/"] = f"https://{domain}/"
        return self

    def __call__(self, url: str):
        """Page servie ; chemin inconnu d'un site connu = 404 (un vrai serveur répond) ; site inconnu = injoignable (None)."""
        from urllib.parse import urlparse
        self.calls.append(url)
        final = self.redirects.get(url, url)
        host = urlparse(final).netloc
        if final not in self.pages:
            known = any(urlparse(u).netloc == host for u in self.pages)
            return Fetched(final, 404, "", "text/html") if known else None
        status, body = self.pages[final]
        ctype = "text/plain" if final.endswith("robots.txt") else "application/xml" if final.endswith(".xml") else "text/html"
        chain, cur = [url], url
        while cur in self.redirects:                                  # chaîne de redirections (http → https, ancien domaine → nouveau…)
            cur = self.redirects[cur]
            chain.append(cur)
        return Fetched(final, status, body, ctype, redirects=len(chain) - 1, chain=tuple(chain), elapsed_ms=self.elapsed_ms, headers=dict(self.headers))


class FakeSearch:
    """`search(query) -> [Hit]` : renvoie des résultats par requête (ou par défaut) ; enregistre les requêtes."""

    def __init__(self, results=None, default=None):
        self.results = results or {}
        self.default = default or []
        self.queries: list[str] = []

    def __call__(self, query: str):
        self.queries.append(query)
        return list(self.results.get(query, self.default))


TODAY = date(2026, 9, 21)
