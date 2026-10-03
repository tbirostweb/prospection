"""Détection des chaînes, réseaux et franchises : SIRENE seul ne suffit pas (un restaurant multi-villes peut avoir chaque adresse sous une
raison sociale distincte). Plusieurs signaux indépendants s'accumulent en `chainConfidence` ; jamais une chaîne sur un nom ressemblant.

Types (`chain_kind`) — la décision d'achat n'est pas au même endroit :
  NATIONAL   décision centralisée (enseigne nationale, grosse entreprise, très nombreux établissements) → forte pénalité
  NETWORK    même enseigne dans plusieurs villes, gérée en réseau → pénalité moyenne
  FRANCHISE  franchisé (petite structure locale sous enseigne de réseau)
Tous sont écartés : le site d'une chaîne ou d'une franchise est fourni par l'enseigne, il n'y a rien à leur vendre.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import htmlinfo
from .naf import LARGE_EMPLOYEE_CODES, brand_of
from .textmatch import fold

CHAIN_THRESHOLD = 0.60
PLURAL_LOCATIONS = re.compile(r"\b(nos|notre|tous nos|toutes nos|les)\s+(restaurants?|agences?|magasins?|boutiques?|salons?|[eé]tablissements?|points?\s+de\s+vente|h[oô]tels?|centres?|garages?|concessions?|adresses?)\b", re.I)
# « franchise » seul ne suffit pas : « franchise en base de TVA » (micro-entreprise), « 0 € de franchise » (pare-brise, assurance) ne sont PAS des franchises.
FRANCHISE_WORDS = re.compile(r"\bfranchis(?:é|ée|és|ées)\b|franchiseur|r[eé]seau\s+de\s+(?:franchise|franchisés)|(?:ouvrir|ouvrez|rejoindre|rejoignez)\s+(?:une|la|notre)\s+franchise|"
                             r"rejoignez\s+(?:notre|le)\s+r[eé]seau|devenir\s+franchis|ouvrir\s+(?:un|votre)\s+(?:restaurant|salon|magasin|agence)", re.I)
LOCATION_PATHS = re.compile(r"/(restaurants?|magasins?|nos[-_]?(?:restaurants|agences|magasins|salons|etablissements|points?[-_]de[-_]vente|adresses)|agences?|points?[-_]de[-_]vente|store[-_]?locator|trouver[-_]un)/", re.I)


@dataclass
class ChainAssessment:
    confidence: float = 0.0
    kind: str | None = None                # NATIONAL | NETWORK | FRANCHISE | None
    name: str | None = None
    evidence: list[dict] = field(default_factory=list)

    @property
    def is_chain(self) -> bool:
        """Chaîne, réseau ou franchise : écarté (le type n'est posé qu'avec des indices suffisants)."""
        return self.kind is not None


def location_list(page: htmlinfo.Page, city: str | None) -> list[str]:
    """Noms de localités listés dans la navigation de l'accueil, SI la commune de l'entreprise en fait partie (liste d'établissements)."""
    if not city:
        return []
    base = page.domain
    groups: dict[str, list[str]] = {}
    for href, text in page.links:
        if href.startswith(("tel:", "mailto:")) or (urlparse(href).hostname or "").lower().removeprefix("www.") != base:
            continue
        t = text.strip()
        if 2 < len(t) <= 28 and len(t.split()) <= 4 and t[:1].isupper() and not re.search(r"accueil|contact|carte|menu|concept|propos|mentions|blog|actualit|recrut|r[eé]serv|commander|panier|connexion|compte", t, re.I):
            groups.setdefault(urlparse(href).path.rsplit("/", 1)[0] if urlparse(href).path.count("/") > 1 else "/", []).append(t)
    for group in groups.values():
        names = list(dict.fromkeys(group))
        if len(names) >= 4 and any(fold(city) == fold(n.split("—")[0].split("-")[0].strip()) or fold(city) in fold(n).split() for n in names):
            return names[:12]
    return []


def assess(p: dict, pages: list[htmlinfo.Page] | None = None, ident=None, *, same_trade_elsewhere: int = 0, same_siren_siblings: int = 0,
           shared_domain_siren: int = 0) -> ChainAssessment:
    """`p` : champs du prospect. Les paramètres `same_*` / `shared_*` viennent de la base (autres prospects connus)."""
    a = ChainAssessment()
    pages = pages or []
    weights: list[float] = []

    def add(code: str, label: str, w: float) -> None:
        a.evidence.append({"code": code, "label": label, "weight": w})
        weights.append(w)

    size, emp, estab = p.get("company_size"), p.get("employee_range"), p.get("establishments_open") or 0
    corroborated = size in ("ETI", "GE") or emp in LARGE_EMPLOYEE_CODES or estab >= 3
    national = False
    label = next((b for lab in (p.get("trade_name"), p.get("company_name")) if (b := brand_of(lab, p.get("naf_code"), corroborated))), None)
    if label:
        add("brand", f"enseigne de chaîne ou de franchise connue (« {label} »)", 0.85)
        a.name = label.title()
        national = True
    if size in ("ETI", "GE"):
        add("size", f"catégorie d'entreprise {size}", 0.7)
        national = True
    if emp in LARGE_EMPLOYEE_CODES:
        add("employees", "50 salariés ou plus", 0.6)
        national = True
    if estab >= 10:
        add("establishments", f"{estab} établissements ouverts sous le même SIREN", 0.65)
        national = True
    elif estab >= 3:
        add("establishments", f"{estab} établissements ouverts sous le même SIREN", 0.4)
    if same_trade_elsewhere >= 2:
        add("same_brand_cities", f"même enseigne trouvée dans {same_trade_elsewhere} autres établissements de la base (raisons sociales distinctes possibles)", 0.5)
    if same_siren_siblings >= 3:
        add("siblings", f"{same_siren_siblings} établissements du même SIREN dans la zone", 0.35)
    if shared_domain_siren >= 1:
        add("shared_domain", f"le même site est utilisé par {shared_domain_siren} autre(s) entreprise(s) (autre SIREN)", 0.3)

    franchise_words = False
    if pages:
        home = pages[0]
        text = " ".join(pg.text[:6000] for pg in pages[:3])
        if PLURAL_LOCATIONS.search(text):
            add("site_locations_text", "le site parle de « nos restaurants / agences / magasins… » (plusieurs lieux)", 0.35)
        if any(LOCATION_PATHS.search(urlparse(h).path + "/") for h, _ in home.links if (urlparse(h).hostname or "").lower().removeprefix("www.") == home.domain):
            add("site_locations_paths", "pages de type /restaurants/, /magasins/, /nos-agences/ sur le site", 0.35)
        if names := location_list(home, p.get("city")):
            add("site_location_list", f"la navigation liste plusieurs localités dont la nôtre ({', '.join(names[:5])}…)", 0.45)
        if ident is not None and ident.org_count >= 3:
            add("schema_locations", f"schema.org : {ident.org_count} entités / lieux déclarés", 0.45)
        franchise_words = bool(FRANCHISE_WORDS.search(text))
        if franchise_words:
            add("franchise_words", "le site parle de franchise / réseau", 0.3)
    a.confidence = round(1 - _prod(1 - w for w in weights), 2) if weights else 0.0
    if a.confidence >= CHAIN_THRESHOLD:
        small_local = size not in ("ETI", "GE") and emp not in LARGE_EMPLOYEE_CODES and estab <= 2
        if national and not small_local:
            a.kind = "NATIONAL"
        elif franchise_words and small_local:
            a.kind = "FRANCHISE"
        elif national:                                # enseigne nationale mais structure locale minuscule : franchisé probable
            a.kind = "FRANCHISE" if small_local else "NATIONAL"
        else:
            a.kind = "NETWORK"
    elif franchise_words and a.confidence >= 0.3:
        a.kind = "FRANCHISE"
    return a


def _prod(it) -> float:
    r = 1.0
    for x in it:
        r *= x
    return r
