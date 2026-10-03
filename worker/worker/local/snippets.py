"""Coordonnées lues dans les EXTRAITS de résultats de recherche (titre + résumé renvoyés par le moteur), sans jamais ouvrir un annuaire.

Pour beaucoup de petites entreprises, aucun site officiel n'est trouvé, mais l'extrait d'un annuaire (PagesJaunes, 118…) affiché par le moteur contient
leur téléphone. On lit UNIQUEMENT ce que le moteur renvoie — aucune requête vers l'annuaire lui-même. Garde-fous d'attribution :
  * l'extrait doit citer un mot distinctif du nom ET la ville ou le code postal de l'entreprise (sinon le numéro peut être celui d'un voisin) ;
  * un numéro fixe doit correspondre à la zone du département (01 Île-de-France, 02 Nord-Ouest, 03 Nord-Est, 04 Sud-Est, 05 Sud-Ouest) ;
    les 08 (numéros surtaxés, souvent des numéros de mise en relation d'annuaire) sont refusés ;
  * plusieurs numéros différents : le plus cité l'emporte et la confiance baisse ; à égalité, aucun n'est retenu ;
  * e-mail : seulement si son domaine reprend le nom de l'entreprise ou s'il s'agit d'une messagerie grand public ; jamais celui de l'annuaire.
Confiance plafonnée à 0,55 : information de seconde main, toujours signalée comme telle (ne suffit jamais pour « Très bon »).
"""
from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from . import htmlinfo
from .contacts import FREEMAIL, IGNORED_DOMAINS, IGNORED_LOCALS, classify_email
from .textmatch import digits, distinctive_tokens, fold, name_tokens, token_fraction

# Zone des numéros fixes par département (DOM : 0262/0269 → 02, 0590/0594/0596 → 05).
_ZONE = {}
for _z, _deps in (("01", "75 77 78 91 92 93 94 95"),
                  ("02", "14 18 22 27 28 29 35 36 37 41 44 45 49 50 53 56 61 72 76 85 974 976"),
                  ("03", "02 08 10 21 25 39 51 52 54 55 57 58 59 60 62 67 68 70 71 80 88 89 90"),
                  ("04", "01 03 04 05 06 07 11 13 15 26 2A 2B 20 30 34 38 42 43 48 63 66 69 73 74 83 84"),
                  ("05", "09 12 16 17 19 23 24 31 32 33 40 46 47 64 65 79 81 82 86 87 971 972 973")):
    for _d in _deps.split():
        _ZONE[_d] = _z
MAX_CONF = 0.55


def phone_plausible(number: str, department: str | None) -> bool:
    """Mobile (06/07) ou 09 : toujours plausible. 08 : refusé. Fixe 01-05 : doit correspondre au département quand il est connu."""
    if number.startswith("08"):
        return False
    if number[:2] in ("06", "07", "09"):
        return True
    zone = _ZONE.get((department or "").upper())
    return zone is None or number[:2] == zone


def _attributed(company: dict, text: str) -> bool:
    """L'extrait parle-t-il bien de CETTE entreprise ? Mot distinctif du nom ET (ville OU code postal)."""
    t = fold(text)
    names = [distinctive_tokens(company.get(k)) or name_tokens(company.get(k)) for k in ("trade_name", "company_name")]
    name_ok = any(n and token_fraction(n, text) >= 0.6 for n in names)
    city, cp = fold(company.get("city")), digits(company.get("postal_code"))
    place_ok = bool((city and f" {city} " in f" {t} ") or (cp and cp in digits(text) and cp in text.replace(" ", "")))
    return name_ok and place_ok


def extract(company: dict, hits: list) -> dict:
    """Coordonnées attribuables à l'entreprise dans des résultats de recherche : même forme que `contacts.extract` (valeurs None si rien)."""
    out: dict = {"phone": None, "phone_confidence": None, "email": None, "email_kind": None, "contact_form": False, "contact_page": None,
                 "contact_source_url": None, "contact_confidence": None, "evidence": []}
    phones: Counter = Counter()
    phone_src: dict[str, list[tuple[str, str]]] = {}
    emails: dict[str, tuple[str, str]] = {}
    name_words = [w for w in (distinctive_tokens(company.get("trade_name")) + distinctive_tokens(company.get("company_name"))) if len(w) >= 4]
    for h in hits or []:
        text = f"{getattr(h, 'title', '')} {getattr(h, 'content', '')}"
        if not text.strip() or not _attributed(company, text):
            continue
        host = (urlparse(getattr(h, "url", "")).hostname or "").lower().removeprefix("www.")
        for raw in htmlinfo.PHONE_RX.findall(text):
            n = htmlinfo.normalize_phone(raw)
            if n and phone_plausible(n, company.get("department")):
                phones[n] += 1
                phone_src.setdefault(n, []).append((host, getattr(h, "url", "")))
        for raw in htmlinfo.EMAIL_RX.findall(text):
            m = raw.lower().strip(".")
            local, _, dom = m.partition("@")
            if not dom or local in IGNORED_LOCALS or dom in IGNORED_DOMAINS or dom == host or host.endswith("." + dom):
                continue                                                    # l'adresse de l'annuaire lui-même n'est pas celle de l'entreprise
            if dom in FREEMAIL or any(w in dom.replace("-", "") for w in name_words):
                emails.setdefault(m, (host, getattr(h, "url", "")))
    if phones:
        ranked = phones.most_common()
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            num, count = ranked[0]
            hosts = {s for s, _u in phone_src[num]}
            conf = 0.45 + (0.10 if len(hosts) >= 2 else 0.0) - (0.10 if len(ranked) > 1 else 0.0)
            conf = round(max(0.3, min(MAX_CONF, conf)), 2)
            host, url = phone_src[num][0]
            out.update(phone=num, phone_confidence=conf, contact_source_url=url)
            out["evidence"].append({"kind": "phone", "value": num, "via": "search_snippet", "source": f"extrait de recherche ({host})", "url": url,
                                    "confidence": conf, "sources": sorted(hosts), "conflicts": len(ranked) - 1})
    if emails:
        mail, (host, url) = next(iter(emails.items()))
        kind = "UNCERTAIN" if mail.split("@")[1] in FREEMAIL else (classify_email(mail, mail.split("@")[1]) or "UNCERTAIN")
        out.update(email=mail, email_kind=kind, contact_source_url=out["contact_source_url"] or url)
        out["evidence"].append({"kind": "email", "value": mail, "category": kind, "via": "search_snippet", "source": f"extrait de recherche ({host})",
                                "url": url, "confidence": 0.45})
    confs = [e["confidence"] for e in out["evidence"]]
    if confs:
        out["contact_confidence"] = round(min(MAX_CONF, max(confs) + (0.04 if len(confs) > 1 else 0.0)), 2)
    return out


def contact_query(company: dict) -> str | None:
    """Requête dédiée aux coordonnées (une seule), pour une entreprise déjà recherchée sans résultat de contact."""
    name = (company.get("trade_name") or company.get("company_name") or "").strip()
    city = (company.get("city") or "").strip()
    return f'"{name}" {city} téléphone' if name and city else None
