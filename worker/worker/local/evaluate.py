"""Mesure de la résolution de site et des contacts sur un jeu de cas vérifiés :  python -m worker.local.evaluate <cases.json> [--contacts <cases.json>]

Métriques HONNÊTES (aucune n'est arrondie en faveur du système) :
  websitePrecision   parmi les sites ACCEPTÉS (CONFIRMED + PROBABLE), part qui est le bon site
  websiteRecall      parmi les entreprises dont le site officiel est connu, part dont le bon site est accepté (UNCERTAIN / NOT_FOUND = raté)
  wrongWebsiteRate   sites acceptés qui sont FAUX, rapportés à TOUS les cas — plus grave qu'un site non trouvé
  uncertainRate      part de cas laissés « incertains » (jamais comptés comme des réussites)
  confirmedOnly*     mêmes mesures avec CONFIRMED seul : un site probable n'est JAMAIS compté comme confirmé
Un jeu de moins de 30 cas n'est pas représentatif : le rapport l'écrit à côté de chaque pourcentage.
Les valeurs attendues ne sont jamais modifiées pour améliorer les mesures.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from .. import net
from . import contacts, htmlinfo, sitefinder

MIN_REPRESENTATIVE = 30


class StaticWeb:
    """`fetch(url)` sur des pages enregistrées : {url: {"status": 200, "html": "...", "final": "https://..."}}. URL absente = injoignable (None)."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        rec = self.pages.get(url)
        if rec is None:
            return None
        final = rec.get("final") or url
        return net.Fetched(final, int(rec.get("status", 200)), rec.get("html", ""), "text/html", chain=(url, final) if final != url else (url,))


class StaticSearch:
    """`search(query) -> [Hit]` : résultats enregistrés par requête (clé « * » = par défaut)."""

    def __init__(self, results: dict):
        self.results = results
        self.queries: list[str] = []

    def __call__(self, query: str):
        self.queries.append(query)
        rows = self.results.get(query, self.results.get("*", []))
        return [sitefinder.Hit(r["url"], r.get("title", ""), r.get("content", "")) for r in rows]


def rate(k: int, n: int) -> str:
    """« 83 % (5/6) », avec la mention « non représentatif » sous 30 cas : jamais un beau pourcentage sur trop peu de cas."""
    if n == 0:
        return "n/a (aucun cas)"
    txt = f"{100 * k / n:.0f} % ({k}/{n})"
    return txt if n >= MIN_REPRESENTATIVE else f"{txt} — échantillon trop petit pour être représentatif"


@dataclass
class SiteResult:
    case_id: str
    kind: str
    status: str
    domain: str | None
    official: str | None
    correct: bool | None        # None : pas accepté


def run_website_case(case: dict, thresholds: sitefinder.Thresholds | None = None) -> SiteResult:
    web, search = StaticWeb(case.get("pages", {})), StaticSearch(case.get("search", {}))
    res = sitefinder.resolve(case["company"], search, web, thresholds or sitefinder.Thresholds(), known_urls=case.get("known_urls"))
    official = (case.get("official_domain") or "").lower().removeprefix("www.") or None
    correct = None
    if res.accepted:
        correct = bool(official) and res.canonical_domain == official
    return SiteResult(case["id"], case.get("kind", ""), res.status, res.canonical_domain, official, correct)


def website_metrics(results: list[SiteResult]) -> dict:
    n = len(results)
    with_site = [r for r in results if r.official]
    accepted = [r for r in results if r.status in ("CONFIRMED", "PROBABLE")]
    confirmed = [r for r in results if r.status == "CONFIRMED"]
    ok = lambda rs: sum(1 for r in rs if r.correct)                                # noqa: E731
    wrong = [r for r in accepted if not r.correct]
    no_site = [r for r in results if not r.official]
    return {
        "cases": n, "with_known_site": len(with_site), "representative": n >= MIN_REPRESENTATIVE,
        "websitePrecision": rate(ok(accepted), len(accepted)), "websiteRecall": rate(ok(with_site), len(with_site)),
        "wrongWebsiteRate": rate(len(wrong), n), "wrongWebsites": [(r.case_id, r.domain, r.official) for r in wrong],
        "uncertainRate": rate(sum(r.status == "UNCERTAIN" for r in results), n),
        "notFoundOnKnownSite": rate(sum(r.status == "NOT_FOUND" for r in with_site), len(with_site)),
        "correctNotFound": rate(sum(r.status == "NOT_FOUND" for r in no_site), len(no_site)),
        "confirmedOnlyPrecision": rate(ok(confirmed), len(confirmed)), "confirmedOnlyRecall": rate(ok([r for r in with_site if r.status == "CONFIRMED"]), len(with_site)),
        "probableCount": sum(r.status == "PROBABLE" for r in results), "confirmedCount": len(confirmed),
        "byStatus": {s: sum(r.status == s for r in results) for s in ("CONFIRMED", "PROBABLE", "UNCERTAIN", "NOT_FOUND", "UNREACHABLE")},
    }


def run_contact_case(case: dict) -> dict:
    pages = [htmlinfo.parse(p["html"], u) for u, p in case["pages"].items()]
    got = contacts.extract(pages, case["site_domain"])
    return {"id": case["id"], "phone": got["phone"], "email": got["email"], "form": got["contact_form"]}


def contact_metrics(cases: list[dict]) -> dict:
    """Précision / rappel des coordonnées sur un jeu VÉRIFIÉ À LA MAIN. `expected` : {"phones": [...], "emails": [...], "form": bool} (vides = aucun attendu)."""
    tp = fp = fn = 0
    errors = []
    for c in cases:
        got = run_contact_case(c)
        exp_p, exp_e = set(c["expected"].get("phones", [])), {e.lower() for e in c["expected"].get("emails", [])}
        for kind, val, exp in (("phone", got["phone"], exp_p), ("email", got["email"], exp_e)):
            if val:
                if val.lower() in exp:
                    tp += 1
                else:
                    fp += 1
                    errors.append((c["id"], kind, val))
            if exp and not val:
                fn += 1
            elif exp and val and val.lower() not in exp:
                fn += 1
    n = len(cases)
    return {"cases": n, "representative": n >= MIN_REPRESENTATIVE, "contactPrecision": rate(tp, tp + fp), "contactRecall": rate(tp, tp + fn),
            "falsePositives": errors, "found": tp + fp}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cases = json.load(open(argv[0], encoding="utf-8"))["cases"]
    m = website_metrics([run_website_case(c) for c in cases])
    print(json.dumps(m, ensure_ascii=False, indent=1))
    if "--contacts" in argv:
        cc = json.load(open(argv[argv.index("--contacts") + 1], encoding="utf-8"))["cases"]
        print(json.dumps(contact_metrics(cc), ensure_ascii=False, indent=1))
    return 1 if m["wrongWebsites"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
