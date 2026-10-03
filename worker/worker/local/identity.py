"""Identité DÉCLARÉE par un site (vérification inversée) : numéros SIREN/SIRET valides, téléphones, codes postaux, noms d'organisation.

`sitefinder` ne se contente pas de demander « ce site ressemble-t-il à cette entreprise ? » : il demande aussi « ce que le site déclare
lui-même (mentions légales, pied de page, schema.org) est-il compatible avec l'entreprise cherchée ? ». Un SIREN différent sur le site
est une preuve NÉGATIVE forte (c'est le site d'une autre société : homonyme, maison mère, prestataire).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import htmlinfo
from .textmatch import digits

_NUM_RX = re.compile(r"(?<!\d)(\d(?:[\s.\-]?\d){8}(?:[\s.\-]?\d{5})?)(?!\d)")
_CONTEXT_RX = re.compile(r"(siren|siret|rcs|r\.c\.s|immatricul|n[°o]\s*(?:de\s*)?(?:tva|siren)|registre du commerce)", re.I)


def luhn_ok(number: str) -> bool:
    s = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        s += d
    return s % 10 == 0


@dataclass
class SiteIdentity:
    sirens: set[str] = field(default_factory=set)
    sirets: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    postal_codes: set[str] = field(default_factory=set)
    org_names: list[str] = field(default_factory=list)
    org_addresses: list[dict] = field(default_factory=list)
    org_count: int = 0                 # nombre d'entités Organization / LocalBusiness / Restaurant… distinctes déclarées (schema.org)


def _walk_types(item: dict) -> list[str]:
    t = item.get("@type")
    return [str(x) for x in (t if isinstance(t, list) else [t])] if t else []


ORG_TYPES = ("organization", "localbusiness", "restaurant", "store", "foodestablishment", "hotel", "lodgingbusiness", "autorepair", "beautysalon",
             "hairsalon", "healthandbeautybusiness", "homeandconstructionbusiness", "plumber", "electrician", "roofingcontractor", "realestateagent",
             "professionalservice", "sportsactivitylocation", "shop", "bakery", "cafeorcoffeeshop", "barorpub", "florist", "medicalbusiness")


def extract(pages: list[htmlinfo.Page]) -> SiteIdentity:
    ident = SiteIdentity()
    names: set[str] = set()
    for pg in pages:
        text = pg.text
        for m in _NUM_RX.finditer(text):
            num = digits(m.group(1))
            around = text[max(0, m.start() - 60):m.start()]
            if len(num) in (9, 14) and _CONTEXT_RX.search(around) and luhn_ok(num):
                (ident.sirens if len(num) == 9 else ident.sirets).add(num)
                ident.sirens.add(num[:9])
        ident.phones.update(pg.phones())
        ident.postal_codes.update(htmlinfo.POSTAL_RX.findall(text[-6000:]))         # pied de page : les derniers caractères
        for item in pg.jsonld:
            if any(t.lower() in ORG_TYPES for t in _walk_types(item)):
                nm = str(item.get("name") or "").strip()
                if nm and nm.lower() not in names:
                    names.add(nm.lower())
                    ident.org_names.append(nm)
                addr = item.get("address")
                if isinstance(addr, dict):
                    ident.org_addresses.append(addr)
                ident.org_count += 1 if nm else 0
                for key in ("branchOf", "department", "location"):
                    sub = item.get(key)
                    if isinstance(sub, list):
                        ident.org_count += len(sub)
    return ident
