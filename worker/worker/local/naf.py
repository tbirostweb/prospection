"""Activités ciblées (codes NAF/APE), activités à exclure, chaînes et franchises.

Rien n'est codé en dur pour une ville ou une activité : le catalogue ci-dessous est un DÉPART, complétable dans les réglages
(`settings.local_activities`) et chaque campagne choisit ses activités — ou saisit directement des codes NAF (« 96.09Z »).
`web` = dépendance de l'activité à sa visibilité en ligne (0-1) : sert au critère « compatibilité business » du score, jamais seul.
"""
from __future__ import annotations

import re

from .textmatch import fold

# clé: (libellé, [codes NAF rév. 2], dépendance au web 0-1)
CATALOG: dict[str, dict] = {
    "restaurants": {"label": "Restaurants", "naf": ["56.10A", "56.10B", "56.10C"], "web": 0.9},
    "bars": {"label": "Bars, cafés", "naf": ["56.30Z"], "web": 0.7},
    "traiteurs": {"label": "Traiteurs", "naf": ["56.21Z"], "web": 0.75},
    "boulangeries": {"label": "Boulangeries, pâtisseries", "naf": ["10.71C", "10.71D", "47.24Z"], "web": 0.5},
    "coiffure": {"label": "Salons de coiffure", "naf": ["96.02A"], "web": 0.85},
    "beaute": {"label": "Instituts de beauté", "naf": ["96.02B", "96.04Z"], "web": 0.9},
    "garages": {"label": "Garages", "naf": ["45.20A", "45.20B", "45.32Z"], "web": 0.8},
    "plombiers": {"label": "Plombiers", "naf": ["43.22A", "43.22B"], "web": 0.8},
    "electriciens": {"label": "Électriciens", "naf": ["43.21A", "43.21B"], "web": 0.8},
    "couvreurs": {"label": "Couvreurs", "naf": ["43.91A", "43.91B"], "web": 0.75},
    "menuisiers": {"label": "Menuisiers", "naf": ["43.32A", "43.32B", "16.23Z"], "web": 0.7},
    "peintres": {"label": "Peintres", "naf": ["43.34Z"], "web": 0.7},
    "macons": {"label": "Maçons, gros œuvre", "naf": ["43.99C", "41.20A", "41.20B"], "web": 0.6},
    "paysagistes": {"label": "Paysagistes", "naf": ["81.30Z"], "web": 0.8},
    "fleuristes": {"label": "Fleuristes", "naf": ["47.76Z"], "web": 0.85},
    "commerces": {"label": "Commerces de proximité", "naf": ["47.11C", "47.21Z", "47.22Z", "47.23Z", "47.25Z", "47.29Z"], "web": 0.6},
    "boutiques": {"label": "Boutiques (mode, déco, beauté…)", "naf": ["47.71Z", "47.72A", "47.72B", "47.75Z", "47.59A", "47.78C"], "web": 0.9},
    "immobilier": {"label": "Agences immobilières", "naf": ["68.31Z"], "web": 0.95},
    "hebergements": {"label": "Hébergements", "naf": ["55.10Z", "55.20Z", "55.30Z"], "web": 1.0},
    "services": {"label": "Services aux entreprises et particuliers", "naf": ["69.20Z", "81.21Z", "81.22Z", "96.01B"], "web": 0.75},
    "associations": {"label": "Associations, clubs", "naf": ["94.99Z", "93.12Z", "93.19Z"], "web": 0.5},
    "photographes": {"label": "Photographes", "naf": ["74.20Z"], "web": 0.95},
    "fitness": {"label": "Salles de sport, coachs", "naf": ["93.13Z", "85.51Z"], "web": 0.9},
    "soins_personnels": {"label": "Tatoueurs, toiletteurs, soins divers", "naf": ["96.09Z"], "web": 0.8},
    "auto_ecoles": {"label": "Auto-écoles", "naf": ["85.53Z"], "web": 0.85},
    "demenageurs": {"label": "Déménageurs", "naf": ["49.42Z"], "web": 0.8},
    "opticiens": {"label": "Opticiens", "naf": ["47.78A"], "web": 0.8},
    "architectes": {"label": "Architectes", "naf": ["71.11Z"], "web": 0.85},
    "avocats": {"label": "Avocats", "naf": ["69.10Z"], "web": 0.8},
    "veterinaires": {"label": "Vétérinaires", "naf": ["75.00Z"], "web": 0.8},
    "sante": {"label": "Kinés, ostéos, dentistes", "naf": ["86.90E", "86.90F", "86.23Z"], "web": 0.8},
    "artisans_art": {"label": "Artisans d'art (bijoux, meubles…)", "naf": ["32.12Z", "31.09B", "16.29Z"], "web": 0.9},
    "evenementiel": {"label": "Événementiel, spectacles", "naf": ["90.01Z", "90.02Z", "93.29Z"], "web": 0.85},
    "cours": {"label": "Cours, formations", "naf": ["85.59A", "85.59B", "85.52Z"], "web": 0.85},
}
_NAF_RX = re.compile(r"^\d{2}\.\d{2}[A-Z]$")

# QUALITÉ plutôt que quantité. FAVORISÉS : métiers où quelques nouveaux clients remboursent un site (artisans, libéraux, services locaux…).
# BRUITÉS : beaucoup d'établissements, beaucoup de chaînes et de sites déjà faits ⇒ gardés SEULEMENT avec un vrai signal (pas de site après une
# vraie recherche, site en panne ou à moderniser, création / reprise récente, réseaux sans site) — sinon écartés du ciblage (`scoring.NOISY_CAP`).
FAVORED = {"plombiers", "electriciens", "couvreurs", "menuisiers", "peintres", "macons", "paysagistes", "garages", "immobilier", "architectes",
           "avocats", "sante", "veterinaires", "photographes", "demenageurs", "auto_ecoles", "hebergements", "artisans_art", "beaute", "coiffure",
           "soins_personnels", "fitness", "evenementiel", "cours", "traiteurs", "services"}
NOISY = {"restaurants", "bars", "boulangeries", "commerces", "boutiques", "opticiens", "associations"}
FAVORED_WEIGHT, NOISY_WEIGHT = 1.15, 0.75


def default_weight(activity_key: str | None) -> float:
    """Poids par défaut d'une activité dans le score (remplacé par `local_options.category_weights` s'il est réglé)."""
    return FAVORED_WEIGHT if activity_key in FAVORED else NOISY_WEIGHT if activity_key in NOISY else 1.0

# Web, logiciel, marketing digital, hébergement : ce ne sont pas des clients cibles (ils font ce que je vends).
EXCLUDED_NAF = {"62.01Z": "développement de logiciels", "62.02A": "conseil informatique", "62.02B": "TMA informatique",
                "62.03Z": "gestion d'installations informatiques", "62.09Z": "activités informatiques", "63.11Z": "hébergement / traitement de données",
                "63.12Z": "portails internet", "58.29A": "édition de logiciels", "58.29B": "édition de logiciels", "58.29C": "édition de logiciels",
                "73.11Z": "agence de publicité", "73.12Z": "régie publicitaire", "70.21Z": "conseil en relations publiques / communication",
                "74.10Z": "design / création graphique",
                # structures sans intérêt commercial pour un site : holdings, sièges, loueurs de biens (SCI patrimoniales), administrations
                "64.20Z": "holding", "70.10Z": "siège social / holding", "68.20A": "location de logements (patrimoine)",
                "68.20B": "location de biens (SCI, patrimoine)", "68.32A": "administration d'immeubles (syndic)", "84.11Z": "administration publique"}
EXCLUDED_LEGAL = (("7", "administration ou collectivité publique"), ("8", "organisme spécialisé (mutuelle, comité, syndicat…)"),
                  ("654", "société civile immobilière (patrimoniale)"))
_EXCLUDED_WORDS = re.compile(
    r"\b(agence\s+web|web\s*agency|agence\s+digitale|agence\s+seo|agence\s+de\s+communication|marketing\s+digital|webdesign\w*|webmaster|"
    r"cr[eé]ation\s+de\s+sites?|cr[eé]ation\s+site|d[eé]veloppeur\s+web|d[eé]veloppement\s+web|r[eé]f[eé]rencement\s+naturel|"
    r"h[eé]bergeur|h[eé]bergement\s+web|freelance\s+web|studio\s+web|community\s+manag\w*)\b", re.I)

# Enseignes BANNIES (chaînes, franchises, réseaux, concessions) : liste par métier dans `franchises.py`, + tes ajouts (Paramètres).
from . import franchises as _franchises  # noqa: E402

CHAIN_PHRASES = _franchises.phrases()
AMBIGUOUS_BRANDS = {fold(b) for b in _franchises.AMBIGUOUS}
BRAND_SECTORS = {fold(b): v for b, v in _franchises.AMBIGUOUS.items()}
_extra_brands: list[str] = []


def set_extra_brands(names) -> None:
    """Enseignes bannies ajoutées dans les réglages (`local_banned_brands`) : bannies dès qu'elles apparaissent dans le nom."""
    global _extra_brands
    _extra_brands = sorted({fold(n) for n in (names or []) if len(fold(n)) >= 3})


_LEGAL_WORDS = {"sarl", "sas", "sasu", "eurl", "sa", "sci", "snc", "ei", "earl", "ets", "etablissements", "et", "cie"}


def brand_of(label: str | None, naf: str | None = None, corroborated: bool = False) -> str | None:
    """Enseigne de chaîne ou de franchise reconnue dans un nom, ou None. Une enseigne ambiguë (« Paul », « Casino ») ne compte que si le nom
    EST l'enseigne (hors forme juridique et ville) dans son métier, ou avec un autre indice (`corroborated`)."""
    n = " " + fold(label) + " "
    if not n.strip():
        return None
    for p in sorted(set(CHAIN_PHRASES) | set(_extra_brands), key=len, reverse=True):          # l'enseigne la plus longue d'abord (« boulangerie paul » avant « paul »)
        if f" {p} " not in n:
            continue
        if p not in AMBIGUOUS_BRANDS or corroborated:
            return p
        rest = [t for t in n.replace(f" {p} ", " ", 1).split() if t not in _LEGAL_WORDS]
        sectors = BRAND_SECTORS.get(p)
        in_sector = not sectors or bool(naf and naf.upper().startswith(tuple(s.upper() for s in sectors)))
        if len(rest) <= 1 and in_sector:                              # « PAUL », « PAUL TROYES », « SARL PAUL » ; pas « CHEZ PAUL LE BISTROT »
            if not rest or rest[0] not in ("chez", "le", "la", "les", "au", "aux", "l", "d", "de", "du"):
                return p
    return None


LARGE_EMPLOYEE_CODES = {"21", "22", "31", "32", "41", "42", "51", "52", "53"}     # 50 salariés et plus


def load_catalog(custom: dict | None = None) -> dict[str, dict]:
    """Catalogue de départ + entrées ajoutées dans les réglages (`{"cle": {"label", "naf": [...], "web": 0.7}}`)."""
    out = {k: dict(v) for k, v in CATALOG.items()}
    for key, v in (custom or {}).items():
        if isinstance(v, dict) and v.get("naf"):
            out[str(key)] = {"label": str(v.get("label") or key), "naf": [str(c).upper() for c in v["naf"]],
                             "web": float(v.get("web", 0.6))}
    return out


def resolve_activities(selection: list[str], catalog: dict | None = None) -> tuple[list[str], list[str]]:
    """Sélection d'une campagne (clés du catalogue ou codes NAF bruts) → (codes NAF uniques, éléments inconnus ignorés)."""
    catalog = catalog or CATALOG
    codes: list[str] = []
    unknown: list[str] = []
    for item in selection or []:
        item = str(item).strip()
        if item in catalog:
            codes += catalog[item]["naf"]
        elif _NAF_RX.match(item.upper()):
            codes.append(item.upper())
        else:
            unknown.append(item)
    return list(dict.fromkeys(codes)), unknown


def activity_of(naf: str | None, catalog: dict | None = None) -> tuple[str | None, str | None, float]:
    """(clé, libellé, dépendance web) de l'activité qui contient ce code NAF ; (None, None, 0.5) sinon (neutre, jamais inventé)."""
    # à l'envers : les activités ajoutées dans les réglages (en fin de catalogue) priment sur le catalogue de base pour un même code NAF
    for key, v in reversed(list((catalog or CATALOG).items())):
        if naf and naf.upper() in v["naf"]:
            return key, v["label"], float(v.get("web", 0.6))
    return None, None, 0.5


def excluded_reason(naf: str | None, name: str | None = None, site_text: str | None = None, legal_category: str | None = None) -> str | None:
    """Motif d'exclusion (agence web, hébergeur, holding, SCI, administration…) ou None. Forme juridique et NAF d'abord, puis le nom et le site."""
    for prefix, label in EXCLUDED_LEGAL:
        if legal_category and str(legal_category).startswith(prefix):
            return f"forme juridique {legal_category} ({label}) : pas une cible"
    if naf and naf.upper() in EXCLUDED_NAF:
        return f"activité NAF {naf.upper()} ({EXCLUDED_NAF[naf.upper()]}) : ce n'est pas une cible"
    for label, text in (("nom", name), ("site", site_text)):
        if text and (m := _EXCLUDED_WORDS.search(text)):
            return f"{label} : « {m.group(0).lower()} » — activité web / marketing, pas une cible"
    return None


def chain_signals(name: str | None, trade_name: str | None = None, company_size: str | None = None,
                  employee_range: str | None = None, establishments_open: int | None = None,
                  legal_category: str | None = None, naf: str | None = None) -> list[str]:
    """Raisons de tenir l'établissement pour une chaîne, une franchise ou une grosse entreprise (liste vide = indépendant probable)."""
    reasons: list[str] = []
    corroborated = company_size in ("ETI", "GE") or employee_range in LARGE_EMPLOYEE_CODES or (establishments_open or 0) >= 3
    for label in (trade_name, name):
        if hit := brand_of(label, naf, corroborated):
            reasons.append(f"enseigne de chaîne ou de franchise (« {hit} »)")
            break
    if company_size in ("ETI", "GE"):
        reasons.append(f"catégorie d'entreprise {company_size}")
    if employee_range in LARGE_EMPLOYEE_CODES:
        reasons.append("50 salariés ou plus")
    if establishments_open is not None and establishments_open >= 6:
        reasons.append(f"{establishments_open} établissements ouverts")
    elif (establishments_open or 0) >= 4 and employee_range in ("11", "12"):
        reasons.append(f"groupe local : {establishments_open} établissements et 10 à 49 salariés (site souvent déjà centralisé)")
    return reasons
