"""Coordonnées PROFESSIONNELLES publiques, lues sur le site officiel identifié — jamais ailleurs, jamais en masse, JAMAIS devinées.

Recherche progressive : page contact → pied de page / accueil → mentions légales → page équipe / entreprise (au plus 5 pages par site, dont
celles déjà lues par le résolveur). Détecte `tel:`, `mailto:`, schema.org (JSON-LD), microdata, formulaires de contact.
Chaque coordonnée garde sa PREUVE (page d'origine, mode de détection, poids) et une confiance : `contactEvidence[]`, `contactConfidence`,
`phoneConfidence`. Aucune adresse n'est jamais fabriquée à partir d'un prénom + domaine.

Catégories d'e-mail : GENERIC_BUSINESS (contact@, info@, accueil@…) · PERSONAL_BUSINESS (nominatif, sur le domaine de l'entreprise) ·
UNCERTAIN (messagerie grand public publiée sur le site officiel, ou forme ambiguë). Une adresse d'un domaine tiers n'est pas la sienne : ignorée.
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
_PERSONAL_RX = re.compile(r"^[a-z]{2,}[._\-][a-z]{2,}$|^[a-z]\.[a-z]{2,}$")
VIA_CONF = {"tel_link": 0.90, "mailto": 0.90, "jsonld": 0.90, "microdata": 0.85, "text": 0.70}


def classify_email(email: str, site_domain: str) -> str | None:
    """'GENERIC_BUSINESS' | 'PERSONAL_BUSINESS' | 'UNCERTAIN', ou None si l'adresse n'est pas exploitable (technique, tierce…)."""
    local, _, domain = email.lower().partition("@")
    if not domain or local in IGNORED_LOCALS or domain in IGNORED_DOMAINS or any(domain.endswith("." + d) for d in IGNORED_DOMAINS):
        return None
    base = local.split("+")[0]
    if domain == site_domain or domain.endswith("." + site_domain) or site_domain.endswith("." + domain):
        if base in GENERIC_PREFIXES or any(base.startswith(p) and base[len(p):].isdigit() for p in GENERIC_PREFIXES):
            return "GENERIC_BUSINESS"
        return "PERSONAL_BUSINESS" if _PERSONAL_RX.match(base) else "UNCERTAIN"
    if domain in FREEMAIL:
        return "UNCERTAIN"
    return None


def _phone_conf(via: str, page_url: str, n_pages: int, kind_priority: bool) -> float:
    c = VIA_CONF.get(via, 0.6)
    if re.search(r"contact", page_url, re.I):
        c += 0.05
    c += 0.05 * min(2, n_pages - 1)                      # retrouvé sur plusieurs pages
    return round(min(0.97, c), 2)


def extract(pages: list[htmlinfo.Page], site_domain: str) -> dict:
    """{'phone','phone_confidence','email','email_kind','contact_form','contact_page','contact_source_url','contact_confidence','evidence'}
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
            kind = classify_email(mail, site_domain)
            key = (rank[kind], via_rank[via]) if kind else None
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


def discover(pages: list[htmlinfo.Page], fetch, site_domain: str) -> list[htmlinfo.Page]:
    """Complète les pages déjà lues avec la page contact / équipe / mentions si aucune coordonnée complète n'a été trouvée (5 pages au plus)."""
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
    return pages
