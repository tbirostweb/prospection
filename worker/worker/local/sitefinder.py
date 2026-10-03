"""Recherche du SITE OFFICIEL d'une entreprise : multi-stratégies PROGRESSIVES, preuves pondérées, vérification inversée.

Règle d'or : un mauvais site associé est PIRE qu'un site non trouvé (il fausse tout le scoring). Donc :
  * jamais le premier résultat pris automatiquement ; annuaires, réseaux sociaux, registres exclus ;
  * chaque candidat est LU (accueil + mentions légales / contact) et reçoit une confiance issue d'une accumulation de PREUVES, chacune
    avec un poids et une explication (`websiteEvidence[]`) : SIREN/SIRET (très fort), adresse (très fort), téléphone (fort), nom + ville
    (moyen), nom seul (faible) ;
  * vérification INVERSÉE : ce que le site déclare lui-même (SIREN, codes postaux, schema.org) contredit-il l'entreprise ? Un autre SIREN est
    une preuve négative forte ;
  * niveaux CONFIRMED (>= 0,90) · PROBABLE (>= 0,80) · UNCERTAIN (>= 0,60) · NOT_FOUND · UNREACHABLE — seuils configurables ;
  * NOT_FOUND s'accompagne de la COUVERTURE de recherche (stratégies essayées / disponibles) et d'une confiance d'absence plafonnée : jamais
    une certitude. Une recherche dégradée (moteur en échec) réduit cette confiance ;
  * domaine canonicalisé (redirections http→https, www, changement de domaine) AVANT toute déduplication ; « mauvais sites » signalés par
    l'utilisateur exclus.
"""
from __future__ import annotations

import re
import socket
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from . import htmlinfo, identity
from .errors import SearchUnavailable
from .textmatch import (contains_number, digits, distinctive_tokens, domain_tokens, fold, name_tokens, normalize_address, token_fraction)

CONFIRMED_MIN = 0.90
PROBABLE_MIN = 0.80
UNCERTAIN_MIN = 0.60
ACCEPT = PROBABLE_MIN                 # compat : en dessous, le site n'est ni retenu ni audité
MAX_CANDIDATES_PER_STRATEGY = 2
MAX_ASSESSED = 6                      # sites lus par entreprise, toutes stratégies confondues (coût borné)
STRATEGY_BUDGET = 6                   # stratégies lancées au plus par passage
EXTRA_PAGES = 2
GUESS_MAX = 8                         # domaines devinés d'après le nom (vérifiés comme n'importe quel candidat) : 0 recherche consommée
GUESS_FETCH = 5                       # domaines devinés EXISTANTS (DNS) lus au plus : des lectures directes, aucune requête aux moteurs
GUESS_TLDS = ("fr", "com")

# Sites qui parlent D'une entreprise sans être SON site (annuaires, registres, agrégateurs d'avis, places de marché, cartes).
NOT_OFFICIAL = {
    "pagesjaunes.fr", "societe.com", "pappers.fr", "verif.com", "infogreffe.fr", "annuaire-entreprises.data.gouv.fr", "entreprises.lefigaro.fr",
    "manageo.fr", "societeinfo.com", "kompass.com", "europages.fr", "cylex.fr", "118712.fr", "mappy.com", "yelp.fr", "yelp.com", "tripadvisor.fr",
    "tripadvisor.com", "thefork.fr", "thefork.com", "lafourchette.com", "ubereats.com", "deliveroo.fr", "just-eat.fr", "google.com", "google.fr",
    "bing.com", "wikipedia.org", "indeed.com", "indeed.fr", "malt.fr", "leboncoin.fr", "booking.com", "airbnb.fr", "airbnb.com", "expedia.fr",
    "petitfute.com", "hoodspot.fr", "restaurantguru.com", "gaultmillau.com", "guide.michelin.com", "planity.com", "treatwell.fr", "doctolib.fr",
    "starofservice.com", "travauxlib.com", "habitatpresto.com", "houzz.fr", "bricoleurs.fr", "batiweb.com", "score3.fr", "b-reputation.com",
    "cybo.com", "fr.linkedin.com", "linkedin.com", "data.gouv.fr", "service-public.fr", "gouv.fr", "lefigaro.fr", "ouest-france.fr", "lest-eclair.fr",
    "linfo.re", "actu.fr", "actulegales.fr", "infonet.fr", "infobel.com", "telephone.city", "lesentreprises.com", "eterritoire.fr", "centre-commercial.fr",
    "rubypayeur.com", "toogoodtogo.com", "gowork.fr", "pple.fr", "datalegal.fr", "infopass.fr", "elle.fr", "garage-auto.info", "118000.fr", "118218.fr", "villepratique.fr",
    "localbiz.fr", "busyplace.fr", "societe.ninja", "bilansgratuits.fr", "sirene.fr", "annuaire-mairie.fr", "leguichetdesformalites.fr", "nosavis.com", "horairesdouverture24.fr",
    "en-ligne.me", "lefrenchguide.com", "franceboulangerie.fr", "boulangeries-patisseries.fr", "restausdefrance.fr", "florajet.com", "meilleur-coiffeur.fr", "myboulange.fr", "annupros.fr", "youtube.com", "pinterest.com", "tiktok.com", "twitter.com", "x.com", "business.site",
}
SOCIAL = {"facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "twitter.com", "x.com", "youtube.com", "pinterest.com"}
LEGAL_LINKS = r"mentions|l[eé]gal|contact|propos|qui[- ]sommes|cgv|nous[- ]trouver|acc[eè]s"
GUESS_PATHS = ("mentions-legales", "contact")

# Poids des preuves (somme plafonnée à 1). Documentés ici pour rester lisibles ET testables.
WEIGHTS = {"siret": 0.60, "siren": 0.50, "address_exact": 0.30, "address_street": 0.18, "phone": 0.25, "name_exact": 0.30, "name_partial": 0.30,
           "domain_name": 0.10, "city": 0.10, "postal": 0.12, "schema_org": 0.20, "legal_name": 0.15, "structured_source": 0.20,
           "activity": 0.04, "other_siren": -0.55, "homonym": -0.30}
NAME_ONLY_CAP = 0.55                  # nom seul (sans ville, code postal, adresse, téléphone ni SIREN) : preuve faible
NO_NAME_CAP = 0.30

Fetcher = Callable[[str], "object | None"]          # url -> net.Fetched | None
Searcher = Callable[[str], list["Hit"]]


@dataclass
class Hit:
    url: str
    title: str = ""
    content: str = ""

    @property
    def domain(self) -> str:
        return (urlparse(self.url).hostname or "").lower().removeprefix("www.")


@dataclass
class Thresholds:
    confirmed: float = CONFIRMED_MIN
    probable: float = PROBABLE_MIN
    uncertain: float = UNCERTAIN_MIN

    @classmethod
    def from_options(cls, opts: dict | None) -> "Thresholds":
        o = (opts or {}).get("website") or {}
        try:
            t = cls(float(o.get("confirmed", CONFIRMED_MIN)), float(o.get("probable", PROBABLE_MIN)), float(o.get("uncertain", UNCERTAIN_MIN)))
        except (TypeError, ValueError):
            return cls()
        return t if 0 < t.uncertain <= t.probable <= t.confirmed <= 1 else cls()      # réglage incohérent : défauts, jamais d'erreur

    def tier(self, conf: float, reachable: bool = True) -> str:
        if conf >= self.probable and not reachable:
            return "UNREACHABLE"
        return "CONFIRMED" if conf >= self.confirmed else "PROBABLE" if conf >= self.probable else "UNCERTAIN" if conf >= self.uncertain else "NOT_FOUND"


def ev(code: str, label: str, weight: float | None = None) -> dict:
    return {"code": code, "label": label, "weight": round(WEIGHTS.get(code, 0.0) if weight is None else weight, 2)}


@dataclass
class Assessment:
    url: str                                  # origine du site FINAL (après redirections)
    domain: str                               # domaine canonique
    confidence: float
    evidence: list[dict] = field(default_factory=list)
    reachable: bool = True
    pages: list[htmlinfo.Page] = field(default_factory=list)
    original_url: str | None = None
    chain: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    identity: identity.SiteIdentity | None = None


@dataclass
class Resolution:
    status: str                               # CONFIRMED | PROBABLE | UNCERTAIN | NOT_FOUND | UNREACHABLE
    url: str | None = None
    confidence: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    socials: list[str] = field(default_factory=list)
    pages: list[htmlinfo.Page] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    strategies_tried: list[str] = field(default_factory=list)
    strategies_total: int = 0
    absence_confidence: float | None = None
    original_url: str | None = None
    final_url: str | None = None
    canonical_domain: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    hits: list = field(default_factory=list)            # tous les résultats vus (titre + extrait) : sert à lire des coordonnées de seconde main
    search_domains: dict = field(default_factory=dict)   # domaine → sort réservé dans les résultats de recherche (« candidat », « annuaire », « réseau social », « nom absent »)
    degraded: bool = False                    # au moins une recherche s'est faite avec un moteur en échec
    identity: identity.SiteIdentity | None = None

    @property
    def accepted(self) -> bool:
        return self.status in ("CONFIRMED", "PROBABLE")

    @property
    def coverage(self) -> float:
        return round(len(self.strategies_tried) / self.strategies_total, 2) if self.strategies_total else 0.0


DIRECTORY_WORDS = re.compile(r"annuaire|entreprise|societe|legal|infogreffe|bilan|pagesjaunes|kbis|siret|siren|verif|infobel|infonet|infopass|pappers|telephone|"
                             r"horaires|avis|classement|guide|villepratique|codepostal|commerce-|commerces-|pros?$", re.I)


def looks_like_directory(domain: str, company: dict) -> bool:
    """Domaine d'annuaire / agrégateur NON encore listé : nom de domaine à mots d'annuaire ET sans mot distinctif du nom de l'entreprise.
    (Constaté en réel : 30 « sites incertains » sur 59 étaient des annuaires parlant de l'entreprise, pas son site.)"""
    core = domain_tokens(domain)
    if not DIRECTORY_WORDS.search(domain.split(".")[0]):
        return False
    names = distinctive_tokens(company.get("company_name")) + distinctive_tokens(company.get("trade_name"))
    return not any(t in core.replace(" ", "") for t in names if len(t) >= 4)


def _in_set(domain: str, domains: set[str]) -> bool:
    parts = domain.split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts) - 1))


def _street_tokens(address: str | None) -> list[str]:
    """Mots de la rue (sans numéro, code postal ni commune) : « 12 rue Emile Zola 10000 TROYES » → emile, zola."""
    a = normalize_address(re.sub(r"\b\d{5}\b.*$", "", address or ""))
    return [t for t in a.split() if not t.isdigit() and t not in {"r", "av", "bd", "rte", "ch", "imp", "pl", "fbg", "allee", "quai", "cours", "zone", "za", "zi"} and len(t) > 2]


def _street_number(address: str | None) -> str | None:
    m = re.match(r"\s*(\d{1,4})\b", address or "")
    return m.group(1) if m else None


def _street_line(address: str | None) -> str:
    return re.sub(r"\s*\b\d{5}\b.*$", "", address or "").strip().title()


def _names(company: dict) -> tuple[str, str]:
    """(nom principal utilisé pour chercher — l'enseigne si elle existe —, raison sociale)."""
    legal = (company.get("company_name") or "").strip()
    trade = (company.get("trade_name") or "").strip()
    return (trade or legal), legal


def _activity_phrase(activity: str) -> str:
    return activity.split(',')[0].split('(')[0].strip().lower()


def build_strategies(company: dict) -> list[tuple[str, str]]:
    """Requêtes progressives (nom, requête), de la plus PRODUCTIVE à la moins productive : la recherche s'arrête dès qu'un site est confirmé,
    donc l'ordre décide du nombre de requêtes consommées.
      1. nom + ville, puis raison sociale + ville : ce que le site affiche en titre ;
      2. nom + téléphone, nom + adresse : le site d'un commerce les affiche sur l'accueil (et l'extrait de résultat les cite) ;
      3. nom + activité + ville (sans guillemets : rattrape les variantes d'écriture du nom) ;
      4. nom + code postal, nom + SIREN : surtout des annuaires (écartés) ; le SIREN n'est guère que sur les mentions légales ;
      5. « site officiel » en dernier recours.
    Ne contient que les stratégies APPLICABLES (pas de « nom + téléphone » sans téléphone) et jamais deux fois la même requête (à la casse, aux
    accents et aux guillemets près ; « nom + activité » est omis quand l'activité est déjà dans le nom : ce serait « nom + ville » sans guillemets).
    Le seul nom n'est JAMAIS cherché (trop d'homonymes)."""
    primary, legal = _names(company)
    city, cp = (company.get("city") or "").strip(), (company.get("postal_code") or "").strip()
    siren = digits(company.get("siren")) or digits(company.get("siret"))[:9]
    phone = digits(company.get("phone"))
    activity = (company.get("activity_label") or "").strip()
    out: list[tuple[str, str]] = []
    if primary and city:
        out.append(("name_city", f'"{primary}" "{city}"'))
    if legal and city and fold(legal) != fold(primary):
        out.append(("legal_city", f'"{legal}" "{city}"'))
    if primary and len(phone) >= 9:
        p = "0" + phone[-9:]
        out.append(("name_phone", f'"{primary}" "{" ".join(p[i:i + 2] for i in range(0, 10, 2))}"'))
    street = _street_line(company.get("address"))
    if primary and street and city:
        out.append(("name_address", f'"{primary}" "{street}" {city}'))
    phrase = _activity_phrase(activity)
    if primary and phrase and city and not set(name_tokens(phrase)) <= set(name_tokens(primary)):
        out.append(("name_activity", f"{primary} {phrase} {city}"))
    if primary and cp:
        out.append(("name_postal", f'"{primary}" "{cp}"'))
    if primary and len(siren) == 9:
        out.append(("name_siren", f'"{primary}" "{siren}"'))
    if primary and city:
        out.append(("name_official", f"{primary} {city} site officiel"))
    seen: set[str] = set()

    def key(q: str) -> str:
        return " ".join(fold(q.replace('"', " ")).split())
    return [(n, q) for n, q in out if not (key(q) in seen or seen.add(key(q)))]


def build_queries(company: dict) -> list[str]:
    return [q for _n, q in build_strategies(company)]


def _snippet_identifiers(company: dict, hit: Hit) -> bool:
    hay = f"{hit.title} {hit.content}"
    for key in ("siret", "siren"):
        n = digits(company.get(key))
        if n and contains_number(hay, n):
            return True
    ph = digits(company.get("phone"))
    return bool(len(ph) >= 9 and contains_number(hay, ph[-9:]))


def _prelim(company: dict, hit: Hit) -> float:
    """Filtre BON MARCHÉ avant de télécharger quoi que ce soit : le résultat de recherche parle-t-il de cette entreprise ?"""
    if _snippet_identifiers(company, hit) and not looks_like_directory(hit.domain, company):
        return 1.0
    hay = f"{hit.title} {hit.content} {domain_tokens(hit.domain)}"
    dt = distinctive_tokens(company.get("company_name")) or name_tokens(company.get("company_name"))
    tt = distinctive_tokens(company.get("trade_name")) or name_tokens(company.get("trade_name"))
    name = max(token_fraction(dt, hay), token_fraction(tt, hay) if tt else 0.0)
    city = 0.3 if company.get("city") and fold(company["city"]) in fold(hay) else 0.0
    return round(min(1.0, name * 0.7 + city), 2)


def digits_groups(text: str) -> set[str]:
    return set(htmlinfo.POSTAL_RX.findall(text or ""))


# Sites gratuits dont l'adresse contient le NOM DU SITE dans le chemin : « moncompte.wixsite.com/salon-lea ». Ce sont de VRAIS sites officiels
# (souvent de très bons prospects de refonte) : autrefois écartés comme des annuaires, ils faisaient passer l'entreprise pour « sans site ».
PATH_SITE_HOSTS = ("wixsite.com",)


def _path_site(p) -> str | None:
    host = (p.hostname or "").lower()
    if host.endswith(tuple("." + h for h in PATH_SITE_HOSTS)):
        seg = [x for x in (p.path or "").split("/") if x]
        return seg[0] if seg else None
    return None


def canonical_domain(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    site = _path_site(p)
    return f"{host}/{site}" if site else host


def _origin(url: str) -> str:
    p = urlparse(url)
    site = _path_site(p)
    return f"{p.scheme}://{p.netloc}/{site}/" if site else f"{p.scheme}://{p.netloc}/"


def score_candidate(company: dict, url: str, hit: Hit | None, pages: list[htmlinfo.Page], *, structured: bool = False,
                    chain: list[str] | None = None, original_url: str | None = None) -> Assessment:
    """Confiance d'UN candidat + preuves lisibles. `structured` : l'URL vient d'une source structurée (OSM…), pas d'une recherche."""
    domain = canonical_domain(url)
    chain = chain or []
    if not pages:                                     # injoignable : on ne juge que sur le résultat de recherche, plafonné
        # Page illisible (anti-bot 403, timeout…) : le résultat de recherche seul ne prouve rien — jamais au-dessus de 0,45 (⇒ pas même « incertain »),
        # sauf source structurée (OSM). Constaté en réel : des annuaires bloqués par anti-bot devenaient des « sites incertains » à 0,60.
        conf = min(0.45, _prelim(company, hit)) if hit else (0.5 if structured else 0.0)
        return Assessment(_origin(url), domain, round(conf, 2), [ev("search_snippet", f"page inaccessible : identification d'après la source seule ({conf:.2f})", 0.0)],
                          False, [], original_url or url, chain)
    text = " ".join(f"{p.title} {' '.join(p.h1)} {p.text}" for p in pages)
    head = " ".join(f"{p.title} {' '.join(p.h1)}" for p in pages[:1]) + " " + (hit.title if hit else "") + " " + domain_tokens(domain)
    tf = fold(text)
    ident = identity.extract(pages)
    evidence: list[dict] = []
    conf = 0.0

    def add(code: str, label: str, weight: float | None = None) -> None:
        nonlocal conf
        e = ev(code, label, weight)
        evidence.append(e)
        conf += e["weight"]

    siret, siren = digits(company.get("siret")), digits(company.get("siren")) or digits(company.get("siret"))[:9]
    strong = False
    if siret and (siret in ident.sirets or contains_number(text, siret)):
        add("siret", "SIRET de l'entreprise présent sur le site (mentions légales)")
        strong = True
    elif siren and len(siren) == 9 and (siren in ident.sirens or contains_number(text, siren)):
        add("siren", "SIREN de l'entreprise présent sur le site")
        strong = True

    dt = distinctive_tokens(company.get("company_name")) or name_tokens(company.get("company_name"))
    tt = distinctive_tokens(company.get("trade_name")) or name_tokens(company.get("trade_name"))
    frac = max(token_fraction(dt, head), token_fraction(tt, head) if tt else 0.0)
    if frac:
        add("name_exact" if frac >= 0.999 else "name_partial", f"nom retrouvé dans le titre / l'en-tête / le domaine ({int(frac * 100)} % des mots)", WEIGHTS["name_exact"] * frac)
        best = tt if tt and token_fraction(tt, head) >= token_fraction(dt, head) else dt
        if best and all(t in domain_tokens(domain).split() or "".join(best) in domain_tokens(domain).replace(" ", "") for t in best):
            add("domain_name", "le nom de domaine reprend le nom de l'entreprise")
    legal_pages = [p for p in pages if re.search(r"mention|legal|cgv|propos", p.url, re.I)]
    if dt and legal_pages and token_fraction(dt, " ".join(p.text for p in legal_pages)) >= 0.999 and len(dt) >= 1:
        add("legal_name", "raison sociale retrouvée dans les mentions légales")

    city, cp = fold(company.get("city")), digits(company.get("postal_code"))
    has_city = bool(city and re.search(rf"\b{re.escape(city)}\b", tf))
    has_cp = bool(cp and cp in digits_groups(text))
    if has_city:
        add("city", f"ville « {company.get('city')} » présente")
    if has_cp:
        add("postal", f"code postal {cp} présent")
    street, num = _street_tokens(company.get("address")), _street_number(company.get("address"))
    if street and token_fraction(street, text) >= 0.6:
        if num and re.search(rf"(?<!\d){num}\s*(?:,|bis|ter)?\s+(?:rue|avenue|av|boulevard|bd|place|chemin|route|impasse|all[ée]e|quai|cours|r\b)", tf + " " + text.lower()) and token_fraction(street, text) >= 0.8:
            add("address_exact", "adresse exacte (numéro et rue) retrouvée sur le site")
            strong = True
        else:
            add("address_street", "rue de l'adresse retrouvée sur le site")
    phone = digits(company.get("phone"))
    if phone and len(phone) >= 9 and any(phone[-9:] == p[-9:] for p in ident.phones):
        add("phone", "téléphone de l'entreprise retrouvé sur le site")
        strong = True
    for pg in pages:
        for item in pg.jsonld:
            nm, addr = fold(str(item.get("name") or "")), item.get("address") if isinstance(item.get("address"), dict) else {}
            if nm and token_fraction(dt, nm) >= 0.6 and (fold(str(addr.get("addressLocality") or "")) == city or digits(str(addr.get("postalCode") or "")) == cp):
                add("schema_org", "données structurées (schema.org) : même nom et même adresse")
                break
        else:
            continue
        break
    if structured:
        add("structured_source", "URL fournie par une source structurée (OSM…) — vérifiée sur le site")
    act = fold(company.get("activity_label")).split()
    if frac and act and any(len(w) > 4 and w in tf for w in act):
        add("activity", "activité cohérente avec le contenu du site")

    contradictions: list[str] = []
    # Vérification INVERSÉE : le site se déclare-t-il autre chose ?
    other = {n for n in ident.sirens if n != siren}
    if other and siren and siren not in ident.sirens:
        add("other_siren", f"⚠ le site indique un AUTRE SIREN ({', '.join(sorted(other)[:2])}) : autre société", WEIGHTS["other_siren"])
        contradictions.append("autre SIREN")
    other_cps = {c for c in ident.postal_codes | digits_groups(text) if c != cp and re.match(r"^(0[1-9]|[1-8]\d|9[0-5]|97\d|98\d)\d{2,3}$", c)}
    if other_cps and not (has_city or has_cp):
        add("homonym", f"⚠ homonyme probable : autre(s) code(s) postal(aux) {', '.join(sorted(other_cps)[:3])} et ni notre ville ni notre code postal", WEIGHTS["homonym"])
        contradictions.append("autre localisation")
    cap = None
    if not strong and not frac:
        cap = NO_NAME_CAP
        evidence.append(ev("cap", "nom de l'entreprise introuvable dans le titre / l'en-tête / le domaine : confiance plafonnée à 0,30", 0.0))
    elif not strong and not (has_city or has_cp or any(e["code"] in ("address_street", "schema_org") for e in evidence)):
        cap = NAME_ONLY_CAP
        evidence.append(ev("cap", f"nom seul, sans ville, code postal, adresse, téléphone ni SIREN : preuve faible, confiance plafonnée à {NAME_ONLY_CAP}", 0.0))
    if cap is not None:
        conf = min(conf, cap)
    final = chain[-1] if chain else url
    return Assessment(_origin(final), domain, round(max(0.0, min(1.0, conf)), 2), evidence, True, pages, original_url or url, chain, contradictions, ident)


def _looks_related(company: dict | None, page: htmlinfo.Page) -> bool:
    """Filtre bon marché : l'accueil mentionne-t-il un mot distinctif du nom, la ville ou le code postal ? Sinon les pages annexes ne changeront rien
    (la confiance serait plafonnée à 0,30) : on ne perd pas 2 requêtes lentes sur un site sans rapport."""
    if company is None:
        return True
    head = f"{page.title} {' '.join(page.h1)} {domain_tokens(page.domain)}"
    dt = distinctive_tokens(company.get("company_name")) or name_tokens(company.get("company_name"))
    tt = distinctive_tokens(company.get("trade_name")) or name_tokens(company.get("trade_name"))
    if token_fraction(dt, head) > 0 or (tt and token_fraction(tt, head) > 0):
        return True
    text = fold(page.text)
    siren = digits(company.get("siren")) or digits(company.get("siret"))[:9]
    return bool((siren and siren in digits(page.text)) or (digits(company.get("phone"))[-9:] and contains_number(page.text, digits(company.get("phone"))[-9:])))


def _load_pages(url: str, fetch: Fetcher, company: dict | None = None) -> tuple[list[htmlinfo.Page], object | None]:
    """Accueil + jusqu'à 2 pages « mentions légales / contact » (liens, sinon chemins usuels). ([], None) si l'accueil est inaccessible."""
    home = fetch(_origin(url))
    if home is None or not getattr(home, "ok", False) or not getattr(home, "text", ""):
        return [], home
    page = htmlinfo.parse(home.text, home.url)
    pages = [page]
    if not _looks_related(company, page):
        return pages, home
    links = htmlinfo.internal_links(page, LEGAL_LINKS)[:EXTRA_PAGES]
    if not links:
        links = [f"{_origin(home.url)}{p}" for p in GUESS_PATHS][:EXTRA_PAGES]
    for extra in links:
        got = fetch(extra)
        if got is not None and getattr(got, "ok", False) and got.text:
            pages.append(htmlinfo.parse(got.text, got.url))
    return pages, home


def _assess(company: dict, url: str, hit: Hit | None, fetch: Fetcher, structured: bool = False) -> Assessment:
    pages, home = _load_pages(url, fetch, company)
    chain = list(getattr(home, "chain", ()) or [])
    final = getattr(home, "url", None) or url
    a = score_candidate(company, final, hit, pages, structured=structured, chain=chain or [final], original_url=url)
    if not pages and home is not None and getattr(home, "status", 200) >= 400 and not a.reachable:
        a.evidence.append(ev("http_error", f"le site répond HTTP {home.status}", 0.0))
    return a


def guess_domains(company: dict) -> list[str]:
    """Domaines plausibles d'après le nom (« boulangerie-dupont.fr », « lepetitfournil.com »…). Aucun n'est cru sur parole : chacun est lu et
    vérifié comme un résultat de recherche (nom + ville / adresse / téléphone / SIREN). Rien si le nom n'a pas de mot distinctif."""
    city = fold(company.get("city")).replace(" ", "-")
    out: list[str] = []
    for label in (company.get("trade_name"), company.get("company_name")):
        toks, dt = name_tokens(label), distinctive_tokens(label)
        if not dt or len(toks) > 5:
            continue
        stems = ["".join(toks), "-".join(toks), "".join(dt)]
        if city:
            stems.append("-".join(toks) + "-" + city)
        for stem in stems:
            if 4 <= len(stem) <= 50:
                out += [f"{stem}.{tld}" for tld in GUESS_TLDS]
    return list(dict.fromkeys(out))[:GUESS_MAX]


def dns_resolves(domains: list[str], timeout: float = 4.0) -> list[str]:
    """Domaines qui existent (résolution DNS), dans l'ordre donné. Borné dans le temps : un résolveur lent ne bloque jamais la campagne."""
    def ok(d: str) -> bool:
        try:
            socket.getaddrinfo(d, 443, proto=socket.IPPROTO_TCP)
            return True
        except OSError:
            return False
    if not domains:
        return []
    pool = ThreadPoolExecutor(max_workers=len(domains))
    futs = {d: pool.submit(ok, d) for d in domains}
    wait(list(futs.values()), timeout=timeout)
    pool.shutdown(wait=False)
    return [d for d, f in futs.items() if f.done() and not f.exception() and f.result()]


def resolve(company: dict, search: Searcher, fetch: Fetcher, thresholds: Thresholds | None = None, strategy_budget: int = STRATEGY_BUDGET,
            known_urls: list[str] | None = None, dns: Callable[[list[str]], list[str]] | None = None) -> Resolution:
    """Trouve et VÉRIFIE le site officiel. Voir `Resolution.status` ; `known_urls` : sites déjà fournis par une source structurée.
    `dns` (résolveur) active les domaines devinés d'après le nom, essayés AVANT toute recherche : un site confirmé ainsi ne coûte aucune requête
    aux moteurs, et si les moteurs sont saturés, un site déjà probable est retenu au lieu d'attendre."""
    th = thresholds or Thresholds()
    strategies = build_strategies(company)
    res = Resolution(status="NOT_FOUND", strategies_total=len(strategies))
    bad = {d.lower().removeprefix("www.") for d in company.get("bad_domains") or []}
    assessed: dict[str, Assessment] = {}                 # clé = domaine CANONIQUE : plusieurs URL d'un même site ne comptent qu'une fois
    seen_hit_domains: set[str] = set()
    guessed: set[str] = set()                           # domaines venus SEULEMENT d'une devinette

    def counted() -> int:
        """Sites lus comptés dans MAX_ASSESSED. Un domaine deviné sans rapport (domaine parqué, autre activité : une seule lecture, l'accueil)
        n'y compte pas : les devinettes ne doivent jamais prendre la place des candidats trouvés par la recherche."""
        return sum(1 for d, a in assessed.items() if not (d in guessed and a.confidence < th.uncertain))

    def consider(url: str, hit: Hit | None, structured: bool = False) -> None:
        if counted() >= MAX_ASSESSED or canonical_domain(url) in bad:
            return
        a = _assess(company, url, hit, fetch, structured)
        if a.domain in bad:
            return
        prev = assessed.get(a.domain)
        if prev is None or a.confidence > prev.confidence:
            assessed[a.domain] = a

    for u in known_urls or []:
        consider(u, None, structured=True)

    def best_conf() -> float:
        return max((a.confidence for a in assessed.values()), default=0.0)

    if dns is not None and best_conf() < th.confirmed:
        for d in dns([g for g in guess_domains(company) if g not in bad])[:GUESS_FETCH]:
            if counted() >= MAX_ASSESSED or best_conf() >= th.confirmed:
                break
            before = set(assessed)
            consider(f"https://{d}/", None)
            for key in set(assessed) - before:
                guessed.add(key)
                assessed[key].evidence.append(ev("guessed_domain", f"domaine deviné d'après le nom ({d}), puis vérifié sur le site", 0.0))

    extra_after_probable = 0
    for name, query in strategies:
        # Site confirmé : on s'arrête. Seule une contradiction sur un candidat PLAUSIBLE (>= probable : il pourrait être le meilleur ou un rival)
        # justifie de continuer ; un homonyme lointain déjà écarté (confiance faible) ne coûtait autrefois que des requêtes inutiles.
        if len(res.strategies_tried) >= strategy_budget or best_conf() >= th.confirmed and not any(
                a.contradictions for a in assessed.values() if a.confidence >= th.probable):
            break
        if th.probable <= best_conf() < th.confirmed:
            extra_after_probable += 1
            if extra_after_probable > 2:
                break                                    # un site probable : deux stratégies de plus pour le confirmer ou le contester, pas davantage
        try:
            hits = search(query) or []
        except SearchUnavailable:
            if best_conf() >= th.probable:
                res.degraded = True                      # moteurs saturés, mais un site probable est déjà vérifié : on le retient
                break
            raise                                        # rien de sûr : on ne conclut pas, le prospect sera repris
        if getattr(getattr(search, "last", None), "degraded", False):
            res.degraded = True
        res.strategies_tried.append(name)
        res.queries.append(query)
        res.hits.extend(hits)
        cands: list[Hit] = []
        if hasattr(search, "note_relevance"):
            search.note_relevance({e for h in hits if _prelim(company, h) > 0 for e in getattr(h, "engines", [])})
        for h in hits:
            d = h.domain
            if not d:
                continue
            if _in_set(d, SOCIAL):
                res.socials.append(h.url)
                res.search_domains.setdefault(d, "réseau social")
            elif _in_set(d, NOT_OFFICIAL) or looks_like_directory(d, company):
                res.search_domains.setdefault(d, "annuaire / registre")
            elif d in bad:
                res.search_domains.setdefault(d, "mauvais site signalé")
            elif d not in seen_hit_domains and _prelim(company, h) > 0:
                seen_hit_domains.add(d)
                cands.append(h)
                res.search_domains.setdefault(d, "candidat")
            else:
                res.search_domains.setdefault(d, "déjà vu" if d in seen_hit_domains else "aucun mot du nom dans le résultat")
        chosen = sorted(cands, key=lambda h: _prelim(company, h), reverse=True)[:MAX_CANDIDATES_PER_STRATEGY]
        room = MAX_ASSESSED - counted()
        if len(chosen) > 1 and room > 1:                 # E/S pures : les candidats d'une même recherche sont lus en parallèle
            with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
                results = list(pool.map(lambda h: _assess(company, h.url, h, fetch), chosen[:room]))
            for a in results:
                if a.domain not in bad and (a.domain not in assessed or a.confidence > assessed[a.domain].confidence):
                    assessed[a.domain] = a
        else:
            for h in chosen:
                consider(h.url, h)
    res.socials = list(dict.fromkeys(res.socials))
    if not assessed:
        res.evidence = [ev("no_candidate", f"aucun résultat pertinent pour {len(res.queries)} recherche(s) (annuaires et réseaux sociaux écartés)", 0.0)]
        _finish_absence(res)
        return res
    ranked = sorted(assessed.values(), key=lambda a: a.confidence, reverse=True)
    for a in ranked:
        res.candidates.append({"url": a.url, "domain": a.domain, "confidence": a.confidence, "reachable": a.reachable, "evidence": a.evidence,
                               "original_url": a.original_url, "redirect_chain": a.chain})
    best = ranked[0]
    rivals = [a for a in ranked[1:] if a.confidence >= th.probable and a.domain != best.domain]
    res.confidence, res.evidence = best.confidence, best.evidence
    tier = th.tier(best.confidence, best.reachable)
    if best.contradictions and tier in ("CONFIRMED", "PROBABLE"):
        tier = "UNCERTAIN"                               # une contradiction de la vérification inversée interdit d'accepter
        res.evidence = best.evidence + [ev("contradiction", "⚠ contradiction détectée par la vérification inversée : non retenu", 0.0)]
    if rivals and tier in ("CONFIRMED", "PROBABLE"):
        tier = "UNCERTAIN"
        res.evidence = best.evidence + [ev("rival", f"⚠ deux sites plausibles ({best.domain}, {rivals[0].domain}) : ambigu", 0.0)]
    res.status = tier
    if tier in ("CONFIRMED", "PROBABLE", "UNREACHABLE"):
        res.url, res.pages, res.identity = best.url, best.pages, best.identity
        res.original_url, res.final_url, res.canonical_domain, res.redirect_chain = best.original_url, best.url, best.domain, best.chain
    if tier == "NOT_FOUND":
        res.evidence = [ev("weak_candidates", f"{len(assessed)} candidat(s) examiné(s), aucun assez fiable (meilleur : {best.confidence:.2f})", 0.0)]
        _finish_absence(res)
    return res


def _finish_absence(res: Resolution) -> None:
    """Confiance d'ABSENCE (plafonnée à 0,85 : jamais une certitude) : couverture des stratégies, réduite si la recherche était dégradée."""
    cov = res.coverage
    conf = 0.10 + 0.75 * cov
    if res.degraded:
        conf *= 0.6
    res.absence_confidence = round(min(0.85, conf), 2)
