"""Normalisation de noms / adresses et comparaison prudente — la base de `websiteConfidence`."""
from __future__ import annotations

import re
import unicodedata

LEGAL_FORMS = {"sarl", "sas", "sasu", "eurl", "sa", "sci", "snc", "scop", "ei", "eirl", "ets", "etablissements", "etablissement",
               "societe", "compagnie", "cie", "groupe", "holding", "selarl", "scm", "sccv", "association", "assoc"}
STOPWORDS = {"le", "la", "les", "l", "de", "du", "des", "d", "et", "en", "au", "aux", "a", "un", "une", "chez", "sur", "the", "and"}
# Mots d'activité : présents sur quantité d'entreprises, ils ne prouvent pas qu'un site est LE bon.
GENERIC_ACTIVITY = {"restaurant", "restauration", "brasserie", "pizzeria", "boulangerie", "patisserie", "boucherie", "charcuterie",
                    "bar", "cafe", "hotel", "salon", "coiffure", "coiffeur", "institut", "beaute", "garage", "automobile", "auto",
                    "plomberie", "plombier", "electricite", "electricien", "couverture", "couvreur", "menuiserie", "menuisier",
                    "peinture", "peintre", "paysagiste", "paysage", "fleuriste", "fleurs", "boutique", "magasin", "commerce",
                    "immobilier", "agence", "cabinet", "entreprise", "artisan", "services", "service", "batiment", "renovation",
                    "traiteur", "epicerie", "primeur", "cave", "vins", "tabac", "presse", "pharmacie", "optique", "club", "sport"}


def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s: str | None) -> list[str]:
    return [t for t in fold(s).split() if t and t not in STOPWORDS]


def name_tokens(name: str | None) -> list[str]:
    """Mots distinctifs d'un nom d'entreprise (sans forme juridique, articles ni mots d'activité seuls)."""
    return [t for t in tokens(name) if t not in LEGAL_FORMS and len(t) > 1]


def distinctive_tokens(name: str | None) -> list[str]:
    """Mots du nom qui identifient VRAIMENT l'entreprise (hors mots d'activité)."""
    return [t for t in name_tokens(name) if t not in GENERIC_ACTIVITY]


def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def contains_number(text: str, number: str) -> bool:
    """`number` (SIRET, SIREN, téléphone) figure-t-il dans `text`, avec ou sans espaces / points / tirets ?"""
    if not number:
        return False
    return number in re.sub(r"(?<=\d)[\s.\-](?=\d)", "", text or "")


def token_fraction(needed: list[str], haystack: str) -> float:
    """Part des mots de `needed` présents (mot entier) dans `haystack`."""
    if not needed:
        return 0.0
    words = set(fold(haystack).split())
    return sum(t in words for t in needed) / len(needed)


def domain_tokens(domain: str) -> str:
    """Nom de domaine sans extension ni « www », découpé en mots quand c'est possible (`boulangerie-martin.fr` → « boulangerie martin »)."""
    host, _, path = (domain or "").lower().removeprefix("www.").partition("/")     # « compte.wixsite.com/salon-lea » : le nom est dans le chemin
    parts = host.split(".")
    core = ".".join(parts[:-1]) if len(parts) > 1 else host
    return fold(f"{core} {path}".replace("-", " "))


def normalize_address(addr: str | None) -> str:
    a = fold(addr)
    for full, short in (("avenue", "av"), ("boulevard", "bd"), ("route", "rte"), ("chemin", "ch"), ("impasse", "imp"), ("place", "pl"),
                        ("faubourg", "fbg"), ("rue", "r")):
        a = re.sub(rf"\b{full}\b", short, a)
    return a
