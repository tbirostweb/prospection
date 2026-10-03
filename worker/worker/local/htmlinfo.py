"""Lecture d'une page HTML : ce que le résolveur de site, l'audit et l'extraction de contacts ont en commun. Aucune requête ici."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

PHONE_RX = re.compile(r"(?<!\d)(?:\+33\s?(?:\(0\)\s?)?|0033\s?(?:\(0\)\s?)?|0)[1-9](?:[\s.\-]?\d{2}){4}(?!\d)")
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
POSTAL_RX = re.compile(r"(?<!\d)(\d{5})(?!\d)")


@dataclass
class Page:
    url: str
    title: str = ""
    h1: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)          # description, robots, viewport, generator, og:*, charset…
    canonical: str | None = None
    lang: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)   # (url absolue, texte)
    text: str = ""
    jsonld: list[dict] = field(default_factory=list)
    raw: str = ""                                     # HTML brut (minuscules) pour les signatures techniques
    has_favicon: bool = False
    doctype: str = ""
    microdata: dict = field(default_factory=dict)      # itemprop → [valeurs] (telephone, email, streetAddress…)
    forms: list[dict] = field(default_factory=list)    # {"has_email": bool, "has_textarea": bool, "action": str}

    @property
    def domain(self) -> str:
        return (urlparse(self.url).hostname or "").lower().removeprefix("www.")

    def phones(self) -> list[str]:
        """Numéros français valides (0X XX XX XX XX, +33, 0033, liens tel:), normalisés en 10 chiffres. Jamais une suite de chiffres quelconque."""
        return [n for n, _via in self.phone_points()]

    def phone_points(self) -> list[tuple[str, str]]:
        """[(numéro normalisé, origine)] — origine : tel_link · jsonld · microdata · text."""
        out: dict[str, str] = {}
        rank = {"tel_link": 0, "jsonld": 1, "microdata": 2, "text": 3}

        def put(raw: str, via: str) -> None:
            n = normalize_phone(raw)
            if n and (n not in out or rank[via] < rank[out[n]]):
                out[n] = via
        for h, _ in self.links:
            if h.lower().startswith("tel:"):
                put(h[4:], "tel_link")
        for item in self.jsonld:
            for key in ("telephone", "phone"):
                if isinstance(item.get(key), str):
                    put(item[key], "jsonld")
            for cp in item.get("contactPoint") if isinstance(item.get("contactPoint"), list) else [item.get("contactPoint")] if isinstance(item.get("contactPoint"), dict) else []:
                if isinstance(cp, dict) and isinstance(cp.get("telephone"), str):
                    put(cp["telephone"], "jsonld")
        for v in self.microdata.get("telephone", []):
            put(v, "microdata")
        for m in PHONE_RX.findall(self.text):
            put(m, "text")
        return list(out.items())

    def email_points(self) -> list[tuple[str, str]]:
        """[(adresse, origine)] — origine : mailto · jsonld · microdata · text."""
        out: dict[str, str] = {}
        rank = {"mailto": 0, "jsonld": 1, "microdata": 2, "text": 3}

        def put(raw: str, via: str) -> None:
            m = raw.lower().strip().strip(".").removeprefix("mailto:").split("?")[0]
            if EMAIL_RX.fullmatch(m) and not m.endswith((".png", ".jpg", ".webp", ".gif", ".svg")) and (m not in out or rank[via] < rank[out[m]]):
                out[m] = via
        for h, _ in self.links:
            if h.lower().startswith("mailto:"):
                put(h, "mailto")
        for item in self.jsonld:
            if isinstance(item.get("email"), str):
                put(item["email"], "jsonld")
        for v in self.microdata.get("email", []):
            put(v, "microdata")
        for m in EMAIL_RX.findall(self.text):
            put(m, "text")
        return list(out.items())

    def emails(self) -> list[str]:
        return [m for m, _via in self.email_points()]


def normalize_phone(raw: str) -> str | None:
    """« +33 3 25 12 34 56 », « 0033325123456 », « 03.25.12.34.56 » → « 0325123456 ». None si ce n'est pas un numéro français plausible
    (mauvaise longueur, préfixe inexistant, suite triviale type 0123456789 ou chiffres répétés)."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0033"):
        d = d[4:]
        d = d if d.startswith("0") and len(d) == 10 else "0" + d
    elif d.startswith("330") and len(d) == 12:
        d = "0" + d[3:]
    elif d.startswith("33") and len(d) == 11:
        d = "0" + d[2:]
    if len(d) != 10 or not re.match(r"^0[1-9]", d):
        return None
    if len(set(d[1:])) <= 2 or d in ("0123456789", "0987654321", "0102030405") or d[1:] in ("123456789", "234567890"):
        return None
    return d


def _jsonld(tree) -> list[dict]:
    out: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text() or "null")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                out.append(item)
    return out


def parse(html: str, url: str) -> Page:
    tree = HTMLParser(html or "")
    page = Page(url=url, raw=(html or "").lower()[:400_000])
    if t := tree.css_first("title"):
        page.title = re.sub(r"\s+", " ", t.text() or "").strip()
    page.h1 = [re.sub(r"\s+", " ", h.text() or "").strip() for h in tree.css("h1")]
    for m in tree.css("meta"):
        key = (m.attributes.get("name") or m.attributes.get("property") or "").lower()
        if key and m.attributes.get("content") is not None:
            page.meta[key] = m.attributes["content"]
        elif m.attributes.get("charset"):
            page.meta["charset"] = m.attributes["charset"]
    if c := tree.css_first('link[rel="canonical"]'):
        page.canonical = c.attributes.get("href")
    page.has_favicon = bool(tree.css_first('link[rel~="icon"]') or tree.css_first('link[rel="shortcut icon"]'))
    if root := tree.css_first("html"):
        page.lang = root.attributes.get("lang")
    page.doctype = (re.match(r"\s*<!doctype[^>]*>", html or "", re.I) or [""])[0].lower()
    page.jsonld = _jsonld(tree)
    for node in tree.css("[itemprop]"):
        key = (node.attributes.get("itemprop") or "").lower()
        if key in ("telephone", "email", "streetaddress", "postalcode", "addresslocality", "name"):
            val = node.attributes.get("content") or node.attributes.get("href") or node.text() or ""
            page.microdata.setdefault(key, []).append(re.sub(r"\s+", " ", val.replace("tel:", "").replace("mailto:", "")).strip())
    for f in tree.css("form"):
        inputs = " ".join((i.attributes.get("type") or "") + " " + (i.attributes.get("name") or "") for i in f.css("input"))
        page.forms.append({"has_email": bool(re.search(r"email|mail", inputs, re.I)), "has_textarea": bool(f.css_first("textarea")),
                           "action": f.attributes.get("action") or ""})
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith(("#", "javascript:")):
            page.links.append((href if href.startswith(("tel:", "mailto:")) else urljoin(url, href), re.sub(r"\s+", " ", a.text() or "").strip()))
    tree.strip_tags(["script", "style", "noscript", "svg"])
    body = tree.body.text(separator=" ") if tree.body else tree.text(separator=" ")
    page.text = re.sub(r"\s+", " ", body).strip()[:30_000]
    return page


def internal_links(page: Page, patterns: str) -> list[str]:
    """Liens internes dont l'URL ou le texte correspond à `patterns` (regex), sans doublon, dans l'ordre de la page."""
    rx = re.compile(patterns, re.I)
    base = page.domain
    out: list[str] = []
    for href, text in page.links:
        if href.startswith(("tel:", "mailto:")):
            continue
        host = (urlparse(href).hostname or "").lower().removeprefix("www.")
        if host == base and (rx.search(urlparse(href).path) or rx.search(text)) and href.split("#")[0] not in out:
            out.append(href.split("#")[0])
    return out
