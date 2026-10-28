"""Coordonnées PROFESSIONNELLES publiques, lues sur le site officiel identifié — jamais ailleurs, jamais en masse, JAMAIS devinées.

Recherche progressive : page contact → pied de page / accueil → mentions légales → page équipe / entreprise (au plus 5 pages par site, dont
celles déjà lues par le résolveur). Détecte `tel:`, `mailto:`, schema.org (JSON-LD), microdata, formulaires de contact.
Chaque coordonnée garde sa PREUVE (page d'origine, mode de détection, poids) et une confiance : `contactEvidence[]`, `contactConfidence`,
`phoneConfidence`. Aucune adresse n'est jamais fabriquée à partir d'un prénom + domaine.

Catégories d'e-mail : GENERIC_BUSINESS (contact@, info@, accueil@…) · PERSONAL_BUSINESS (nominatif, sur le domaine de l'entreprise) ·
UNCERTAIN (messagerie grand public ou domaine différent de celui du site, publiés sur le site, ou forme ambiguë). L'adresse d'une plateforme,
d'une agence web / d'un hébergeur / d'un éditeur technique (contexte « réalisé par », crédit en pied de page…) n'est pas la sienne : ignorée.
Une adresse du domaine du site passe toujours avant une adresse d'un autre domaine.
"""
from __future__ import annotations

import re

from . import htmlinfo

GENERIC_PREFIXES = ("contact", "info", "infos", "bonjour", "hello", "commercial", "accueil", "reservation", "reservations", "secretariat",
                    "devis", "boutique", "commande", "commandes", "administration", "direction", "cabinet", "agence", "magasin", "restaurant", "salon")
FREEMAIL = {"gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr", "yahoo.com", "orange.fr", "wanadoo.fr", "free.fr",
            "sfr.fr", "laposte.net", "live.fr", "icloud.com", "neuf.fr", "bbox.fr", "aol.com", "msn.com", "proton.me", "protonmail.com"}
IGNORED_LOCALS = {"noreply", "no-reply", "donotreply", "webmaster", "postmaster", "abuse", "privacy", "dpo", "rgpd", "support-wix", "sentry", "hostmaster"}
IGNORED_DOMAINS = {"sentry.io", "wixpress.com", "example.com", "domaine.com", "votredomaine.fr", "email.com", "sentry-next.wixpress.com", "exemple.fr", "exemple.com"}
CONTACT_LINKS = r"contact|nous[- ]contacter|nous[- ]joindre|nous[- ]trouver|acc[eè]s"
TEAM_LINKS = r"[eé]quipe|team|entreprise|soci[eé]t[eé]|qui[- ]sommes|a[- ]propos|notre[- ]histoire"
LEGAL_LINKS = r"mentions|l[eé]gal"
MAX_SITE_PAGES = 5
EXTRA_PAGES = 3                                      # relecture (`discover(deep=True)`) : pages en plus de MAX_SITE_PAGES
EXTRA_PATHS = ("/contact", "/nous-contacter", "/contactez-nous", "/mentions-legales", "/mentions-legales/", "/contact/")
# Contexte qui désigne l'AGENCE / l'hébergeur / l'éditeur technique du site : son adresse n'est pas celle de l'entreprise.
_CREDIT_RX = re.compile(r"r[ée]alis[ée]\w*\s+par|conception|cr[ée]ation|cr[ée][ée]\w*\s+par|d[ée]velopp[ée]\w*\s+par|h[ée]berge\w*|h[ée]berg[ée]\w*\s+par|"
                        r"web\s*-?design\w*|agence|site\s+web\s+par|propuls[ée]\w*\s+par|powered\s+by|made\s+by|designed\s+by|build\s+by", re.I)
_CREDIT_WINDOW = 80
_PERSONAL_RX = re.compile(r"^[a-z]{2,}[._\-][a-z]{2,}$|^[a-z]\.[a-z]{2,}$")
VIA_CONF = {"tel_link": 0.90, "mailto": 0.90, "jsonld": 0.90, "microdata": 0.85, "text": 0.70}


def classify_email(email: str, site_domain: str, foreign: bool = True) -> str | None:
    """'GENERIC_BUSINESS' | 'PERSONAL_BUSINESS' | 'UNCERTAIN', ou None si l'adresse n'est pas exploitable (technique, plateforme…).
    Domaine ≠ celui du site (hors messagerie grand public) : UNCERTAIN si `foreign`, sinon None ; à l'appelant d'écarter l'agence web / l'hébergeur."""
    local, _, domain = email.lower().partition("@")
    if not domain or local in IGNORED_LOCALS or domain in IGNORED_DOMAINS or any(domain.endswith("." + d) for d in IGNORED_DOMAINS):
        return None
    base = local.split("+")[0]
    from .sitefinder import platform_host
    if domain != site_domain and platform_host(site_domain) and (domain == platform_host(site_domain) or platform_host(domain)):
        return None                                  # site « quentin-cheron.artizo.fr » : l'adresse de la plateforme (ou d'un autre artisan) n'est pas la sienne
    if domain == site_domain or domain.endswith("." + site_domain) or site_domain.endswith("." + domain):
        if base in GENERIC_PREFIXES or any(base.startswith(p) and base[len(p):].isdigit() for p in GENERIC_PREFIXES):
            return "GENERIC_BUSINESS"
        return "PERSONAL_BUSINESS" if _PERSONAL_RX.match(base) else "UNCERTAIN"
    if domain in FREEMAIL:
        return "UNCERTAIN"
    return "UNCERTAIN" if foreign else None


def _same_site(domain: str, site_domain: str) -> bool:
    return domain == site_domain or domain.endswith("." + site_domain) or site_domain.endswith("." + domain)


def _credit_domains(page: htmlinfo.Page) -> set[str]:
    """Domaines cités comme crédit (agence, hébergeur) : lien externe dont le texte ou le voisinage dans le pied de page porte « réalisé par »…"""
    from urllib.parse import urlparse
    foot = page.footer or ""
    out: set[str] = set()
    for href, text in page.links:
        host = (urlparse(href).hostname or "").lower().removeprefix("www.")
        if not host or host == page.domain or href.lower().startswith(("mailto:", "tel:")):
            continue
        if _CREDIT_RX.search(text or ""):
            out.add(host)
            continue
        label = (text or "").strip()
        if label and foot:
            i = foot.find(label)
            if i >= 0 and _CREDIT_RX.search(foot[max(0, i - _CREDIT_WINDOW):i + len(label) + _CREDIT_WINDOW]):
                out.add(host)
    return out


def is_agency_email(page: htmlinfo.Page, mail: str) -> bool:
    """L'adresse (d'un autre domaine que le site) est-elle celle de l'agence web / de l'hébergeur / de l'éditeur ? Contexte « réalisé par »,
    « conception », « création », « hébergé par », « webdesign », « agence »… à proximité dans le texte, ou domaine d'un lien de crédit."""
    domain = mail.partition("@")[2]
    if any(domain == d or d.endswith("." + domain) or domain.endswith("." + d) for d in _credit_domains(page)):
        return True
    for zone in (page.text or "", page.footer or ""):
        low = zone.lower()
        i = low.find(mail)
        while i >= 0:
            if _CREDIT_RX.search(zone[max(0, i - _CREDIT_WINDOW):i + len(mail) + _CREDIT_WINDOW]):
                return True
            i = low.find(mail, i + 1)
    return False


def _phone_conf(via: str, page_url: str, n_pages: int, kind_priority: bool) -> float:
    c = VIA_CONF.get(via, 0.6)
    if re.search(r"contact", page_url, re.I):
        c += 0.05
    c += 0.05 * min(2, n_pages - 1)                      # retrouvé sur plusieurs pages
    return round(min(0.97, c), 2)


def extract(pages: list[htmlinfo.Page], site_domain: str, foreign: bool = True) -> dict:
    """`foreign` : garder (UNCERTAIN) les adresses d'un autre domaine que le site, hors agence / hébergeur. {'phone','phone_confidence','email','email_kind','contact_form','contact_page','contact_source_url','contact_confidence','evidence'}
    (valeurs None / False / [] quand rien de fiable n'a été lu)."""
    out: dict = {"phone": None, "phone_confidence": None, "email": None, "email_kind": None, "contact_form": False, "contact_page": None,
                 "contact_source_url": None, "contact_confidence": None, "evidence": []}
    if not pages:
        return out
    home = pages[0]
    links = htmlinfo.internal_links(home, CONTACT_LINKS)
    out["contact_page"] = links[0] if links else next((p.url for p in pages if re.search(r"contact", p.url, re.I)), None)

    # e-mails : le meilleur par catégorie puis par mode de détection
    rank = {"GENERIC_BUSINESS": 0, "PERSONAL_BUSINESS": 1, "UNCERTAIN": 2}
    via_rank = {"mailto": 0, "jsonld": 1, "microdata": 2, "text": 3}
    best = None
    for page in pages:
        for mail, via in page.email_points():
            kind = classify_email(mail, site_domain, foreign)
            dom = mail.partition("@")[2]
            tier = 0 if _same_site(dom, site_domain) else 1 if dom in FREEMAIL else 2     # l'adresse du domaine du site passe toujours avant
            if kind and tier == 2 and is_agency_email(page, mail):
                kind = None
            key = (tier, rank[kind], via_rank[via]) if kind else None
            if key and (best is None or key < best[0]):
                best = (key, mail, kind, via, page.url)
    email_conf = None
    if best:
        _k, mail, kind, via, url = best
        email_conf = round(min(0.97, VIA_CONF[via] - (0.15 if kind == "UNCERTAIN" else 0.0) + (0.05 if re.search(r"contact", url, re.I) else 0.0)), 2)
        out.update(email=mail, email_kind=kind, contact_source_url=url)
        out["evidence"].append({"kind": "email", "value": mail, "category": kind, "via": via, "url": url, "confidence": email_conf,
                                "freemail": mail.split("@")[1] in FREEMAIL})

    # téléphones : celui qui revient le plus, par le meilleur mode de détection
    seen: dict[str, list[tuple[str, str]]] = {}
    for page in sorted(pages, key=lambda p: 0 if re.search(r"contact", p.url, re.I) else 1):
        for n, via in page.phone_points():
            seen.setdefault(n, []).append((via, page.url))
    if seen:
        num = max(seen, key=lambda n: (len({u for _v, u in seen[n]}), -min(("tel_link", "jsonld", "microdata", "text").index(v) for v, _u in seen[n])))
        via, url = min(seen[num], key=lambda t: ("tel_link", "jsonld", "microdata", "text").index(t[0]))
        conf = _phone_conf(via, url, len({u for _v, u in seen[num]}), True)
        out.update(phone=num, phone_confidence=conf)
        out["contact_source_url"] = out["contact_source_url"] or url
        out["evidence"].append({"kind": "phone", "value": num, "via": via, "url": url, "confidence": conf, "pages": len({u for _v, u in seen[num]})})

    # formulaire de contact officiel
    for p in pages:
        if any(f["has_textarea"] and f["has_email"] for f in p.forms) or (p.forms and re.search(r"contact", p.url, re.I) and any(f["has_textarea"] for f in p.forms)):
            out["contact_form"] = True
            out["contact_page"] = out["contact_page"] or p.url
            out["evidence"].append({"kind": "form", "value": p.url, "via": "form", "url": p.url, "confidence": 0.7})
            break
    if out["contact_page"] and not any(e["kind"] == "page" for e in out["evidence"]):
        out["evidence"].append({"kind": "page", "value": out["contact_page"], "via": "link", "url": home.url, "confidence": 0.6})

    confs = [e["confidence"] for e in out["evidence"] if e["kind"] in ("phone", "email", "form")]
    if confs:
        out["contact_confidence"] = round(min(0.98, max(confs) + (0.04 if len(confs) > 1 else 0.0)), 2)
    elif out["contact_page"]:
        out["contact_confidence"] = 0.4
    return out


def discover(pages: list[htmlinfo.Page], fetch, site_domain: str, deep: bool = False) -> list[htmlinfo.Page]:
    """Complète les pages déjà lues avec la page contact / équipe / mentions si aucune coordonnée complète n'a été trouvée (5 pages au plus).
    `deep` (relecture d'un site sans e-mail) : jusqu'à EXTRA_PAGES pages de plus, dont /contact et /mentions-legales même sans lien."""
    pages = list(pages)
    if not pages:
        return pages
    read = {p.url.split("#")[0] for p in pages}

    def missing() -> bool:
        c = extract(pages, site_domain)
        return not (c["phone"] and c["email"])
    for pattern in (CONTACT_LINKS, LEGAL_LINKS, TEAM_LINKS):
        if len(pages) >= MAX_SITE_PAGES or not missing():
            break
        for link in htmlinfo.internal_links(pages[0], pattern):
            if link in read:
                continue
            got = fetch(link)
            read.add(link)
            if got is not None and getattr(got, "ok", False) and getattr(got, "text", ""):
                pages.append(htmlinfo.parse(got.text, got.url))
                break
    if deep:
        from urllib.parse import urlsplit
        sp = urlsplit(pages[0].url)
        base = f"{sp.scheme}://{sp.netloc}"
        have = {u.rstrip("/") for u in read}
        for path in EXTRA_PATHS:
            if len(pages) >= MAX_SITE_PAGES + EXTRA_PAGES or c_has_email(pages, site_domain):
                break
            link = base + path
            if link.rstrip("/") in have:
                continue
            have.add(link.rstrip("/"))
            got = fetch(link)
            if got is not None and getattr(got, "ok", False) and getattr(got, "text", ""):
                pages.append(htmlinfo.parse(got.text, got.url))
    return pages


def c_has_email(pages: list[htmlinfo.Page], site_domain: str) -> bool:
    return bool(extract(pages, site_domain)["email"])


def extract_unverified(pages: list[htmlinfo.Page], site_domain: str, company: dict) -> dict:
    """Site « à trancher » (UNCERTAIN, ou refusé faute de preuve) : SEULEMENT l'e-mail, et seulement si le site est bien lié à l'entreprise —
    domaine contenant un mot distinctif du nom, OU page affichant le nom distinctif ET la ville / le code postal (`snippets._attributed`). Jamais
    le téléphone ni le formulaire. E-mail toujours UNCERTAIN, sur le domaine du site ou une messagerie grand public (pas d'autre domaine)."""
    from . import snippets
    from .textmatch import distinctive_tokens, fold
    out = extract([], site_domain)
    if not pages or not site_domain:
        return out
    label = fold(site_domain.rsplit(".", 1)[0]).replace(" ", "")
    words = [w for k in ("trade_name", "company_name") for w in distinctive_tokens(company.get(k)) if len(w) >= 4]
    by_domain = any(w in label for w in words)
    by_page = any(snippets._attributed(company, f"{p.title} {' '.join(p.h1)} {p.text} {p.footer}") for p in pages)
    if not (by_domain or by_page):
        return out
    c = extract(pages, site_domain, foreign=False)
    if not c["email"]:
        return out
    out.update(email=c["email"], email_kind="UNCERTAIN", contact_source_url=c["contact_source_url"])
    for e in c["evidence"]:
        if e["kind"] == "email":
            out["evidence"].append({**e, "category": "UNCERTAIN", "confidence": round(min(e["confidence"], 0.55), 2), "site_verified": False,
                                    "source": "site non vérifié (" + ("domaine = nom" if by_domain else "nom + ville sur la page") + ")"})
    out["contact_confidence"] = out["evidence"][0]["confidence"]
    return out
