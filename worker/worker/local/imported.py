"""Fiches IMPORTÉES (import CSV, `pipeline_stage='IMPORTED'`) : vérification du site déclaré, âge du site, contacts manquants, score.

    python -m worker.local.imported        # une passe (appelée aussi à la fin de chaque passage de `worker.local.run`)

Aucune requête de moteur de recherche : seul le site DÉCLARÉ par le fichier est lu (accueil + mentions légales / contact).
Le site est-il bien celui de l'entreprise ? Mêmes preuves que la recherche (`sitefinder.score_candidate` : SIREN/SIRET, nom, adresse,
téléphone…) plus trois preuves propres à l'import :
  * l'e-mail de la fiche est écrit sur le site ;
  * l'e-mail de la fiche est sur le domaine du site (contact@son-site.fr, jamais une messagerie gratuite) ;
  * un réseau social de la fiche est lié depuis le site.
Confiance >= seuil « confirmé » : CONFIRMED · >= seuil appris (sinon « probable ») : PROBABLE · sinon : site gardé « à vérifier »
(`site_review='PENDING'`) : c'est TOI qui tranches dans l'application (« C'est bien son site » / « Ce n'est pas son site »). Chacune de tes
décisions est enregistrée (`local_site_reviews`) et ajuste le seuil des prochains sites (`learn_threshold`), jamais sous 0,60.
Un site récent (mention « © » >= LOCAL_RECENT_SITE_YEAR) est écarté par le score, même avec un e-mail.
Une fiche traitée est terminée (`DONE`) et prête dès qu'elle a un e-mail, un téléphone ou un formulaire (READY : exception des imports).
"""
from __future__ import annotations

import json
import time

from .. import db
from ..config import log
from . import audit as audit_mod, contacts, errors, fetch as fetch_mod, sitefinder, socials as socials_mod, store
from .sitefinder import ev

BATCH = 20
EMAIL_ON_SITE = 0.15
EMAIL_DOMAIN = 0.20
SOCIAL_LINK = 0.10
LEARN_MIN_REVIEWS = 8          # décisions nécessaires avant d'ajuster le seuil
LEARN_MIN_ABOVE = 5            # sites confirmés au-dessus du seuil proposé
LEARN_FLOOR = 0.60             # le seuil appris ne descend jamais plus bas


def _j(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def _list(v) -> list:
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v) or []
        except ValueError:
            return []
    return v or []


def learn_threshold(conn, default: float) -> tuple[float, str]:
    """Seuil de confirmation automatique appris de TES décisions : le plus bas niveau de confiance au-dessus duquel tous les sites que tu as
    jugés étaient bien les bons (au moins LEARN_MIN_ABOVE), borné entre LEARN_FLOOR et `default`. Trop peu de décisions : `default`."""
    rows = db.fetch_all(conn, "SELECT verdict, confidence FROM local_site_reviews WHERE confidence IS NOT NULL")
    if len(rows) < LEARN_MIN_REVIEWS:
        return default, f"{len(rows)}/{LEARN_MIN_REVIEWS} décisions : seuil par défaut {default:.2f}"
    judged = sorted(((float(r["confidence"]), r["verdict"] == "CONFIRMED") for r in rows), reverse=True)
    best = default
    ok = 0
    for conf, good in judged:
        if not good:
            break
        ok += 1
        if ok >= LEARN_MIN_ABOVE:
            best = min(best, conf)
    best = max(LEARN_FLOOR, best)
    return best, f"seuil appris {best:.2f} d'après {len(rows)} décisions"


def verify_site(company: dict, url: str, fetch, *, email: str | None = None, socials: list | None = None,
                thresholds: sitefinder.Thresholds | None = None, learned: float | None = None) -> dict:
    """Vérifie le site DÉCLARÉ (sans recherche). {'status','url','domain','confidence','evidence','pages','reachable'}."""
    th = thresholds or sitefinder.Thresholds()
    a = sitefinder._assess(company, url, None, fetch)
    conf, evidence = a.confidence, list(a.evidence)
    if a.reachable and a.pages:
        text = " ".join(f"{p.text} {p.raw}" for p in a.pages).lower()
        mail = (email or "").strip().lower()
        if mail and mail in text:
            evidence.append(ev("email_on_site", "l'e-mail de la fiche est écrit sur le site", EMAIL_ON_SITE))
            conf += EMAIL_ON_SITE
        dom = mail.rsplit("@", 1)[-1] if "@" in mail else ""
        if dom and dom not in contacts.FREEMAIL and (dom == a.domain or dom.endswith("." + a.domain) or a.domain.endswith("." + dom)):
            evidence.append(ev("email_domain", f"l'e-mail de la fiche est sur le domaine du site ({dom})", EMAIL_DOMAIN))
            conf += EMAIL_DOMAIN
        wanted = {socials_mod_norm(x.get("url") if isinstance(x, dict) else x) for x in socials or []} - {""}
        linked = {socials_mod_norm(u) for p in a.pages for u, _t in p.links}
        if wanted & linked:
            evidence.append(ev("social_link", "un réseau social de la fiche est lié depuis le site", SOCIAL_LINK))
            conf += SOCIAL_LINK
    conf = round(max(0.0, min(1.0, conf)), 2)
    if not a.reachable:
        status = "UNREACHABLE"
    elif a.contradictions:
        status = "UNCERTAIN"                     # le site se déclare autre chose (autre SIREN, autre ville) : à toi de trancher
        evidence.append(ev("contradiction", "⚠ contradiction : " + ", ".join(a.contradictions), 0.0))
    elif a.verdict in ("other_company", "group_site", "maintenance", "parked"):
        status = "UNCERTAIN"                     # autre entreprise, site de groupe, page de maintenance (règle d'identité) : à toi de trancher
    elif conf >= th.confirmed:
        status = "CONFIRMED"
    elif conf >= (learned if learned is not None else th.probable):
        status = "PROBABLE"
    else:
        status = "UNCERTAIN"
    return {"status": status, "url": a.url, "domain": a.domain, "confidence": conf, "evidence": evidence, "pages": a.pages, "reachable": a.reachable}


def socials_mod_norm(url: str | None) -> str:
    """URL de profil comparable : sans protocole, « www. », paramètres ni « / » final, en minuscules."""
    u = (url or "").strip().lower().split("?")[0].split("#")[0].rstrip("/")
    for prefix in ("https://", "http://"):
        u = u.removeprefix(prefix)
    return u.removeprefix("www.").removeprefix("m.").removeprefix("fr-fr.")


def _company(row: dict) -> dict:
    return {k: row.get(k) for k in ("siret", "siren", "company_name", "trade_name", "city", "postal_code", "address", "phone", "activity_label")}


def process(conn, row: dict, fetch, ctx, learned: float | None) -> str:
    """Traite UNE fiche importée. Renvoie le statut de site retenu (ou « none » sans site déclaré)."""
    pid = row["id"]
    outcome = "none"
    url = row.get("website_url")
    review = row.get("site_review")
    if url and review != "REJECTED":
        if review == "CONFIRMED":                # tu as déjà tranché : on lit seulement le site (âge, contacts)
            pages, _home = sitefinder._load_pages(url, fetch, _company(row))
            status = row.get("website_status") or "CONFIRMED"
            res = {"status": status, "pages": pages, "domain": sitefinder.canonical_domain(url)}
        else:
            res = verify_site(_company(row), url, fetch, email=row.get("email"), socials=_list(row.get("social_links")),
                              thresholds=ctx.site_thresholds, learned=learned)
            pending = res["status"] == "UNCERTAIN"
            db.execute(conn, """UPDATE local_prospects SET website_status=%s, website_url=%s, website_confidence=%s, website_evidence=%s,
                                canonical_domain=%s, website_checked_at=UTC_TIMESTAMP(), site_review=%s WHERE id=%s""",
                       (res["status"], res["url"] if res["reachable"] else url, res["confidence"], _j({"evidence": res["evidence"], "source": "import CSV"}),
                        res["domain"], "PENDING" if pending else None, pid))
            store.set_provenance(conn, pid, "website", {"source": "import CSV", "status": res["status"], "url": url, "confidence": res["confidence"],
                                                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        outcome = res["status"]
        pages = res["pages"]
        if pages:
            if year := audit_mod.site_year(" ".join(p.raw for p in pages)):
                db.execute(conn, "UPDATE local_prospects SET site_year=%s WHERE id=%s", (year, pid))
            if res["status"] in ("CONFIRMED", "PROBABLE"):
                if store.is_do_not_contact(conn, domain=res["domain"]):
                    db.execute(conn, "UPDATE local_prospects SET do_not_contact=1, status='DO_NOT_CONTACT' WHERE id=%s", (pid,))
                pages = contacts.discover(pages, fetch, res["domain"])
                store.add_contacts(conn, pid, row, contacts.extract(pages, res["domain"]))     # complète SEULEMENT ce qui manque
                known = _list(row.get("social_links"))
                store.save_socials(conn, pid, socials_mod.merge(known, socials_mod.from_pages(pages)))
    from .runner import _rescore
    fresh = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    _rescore(conn, fresh, ctx)
    store.set_provenance(conn, pid, "contacts", {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "via": "import CSV"})
    db.execute(conn, """UPDATE local_prospects SET pipeline_stage='DONE', deep_enriched_at=COALESCE(deep_enriched_at, UTC_TIMESTAMP()),
                        attempts=0, next_retry_at=NULL, error_category=NULL, last_error=NULL WHERE id=%s""", (pid,))
    return outcome


def run(conn, fetch=None, deadline: float | None = None, limit: int = BATCH) -> dict:
    """Une passe bornée sur les fiches importées pas encore traitées. Une fiche en échec n'arrête jamais les autres (reprise au passage suivant)."""
    from .runner import Ctx
    rows = db.fetch_all(conn, """SELECT * FROM local_prospects WHERE pipeline_stage='IMPORTED' AND do_not_contact=0
                                 AND (next_retry_at IS NULL OR next_retry_at <= UTC_TIMESTAMP()) ORDER BY id LIMIT %s""", (int(limit),))
    stats: dict = {"processed": 0, "errors": 0}
    if not rows:
        return stats
    ctx = Ctx(conn, {"radius_km": 50})
    learned, why = learn_threshold(conn, ctx.site_thresholds.probable)
    stats["threshold"] = why
    fetch = fetch or fetch_mod.Fetcher()
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            break
        try:
            key = process(conn, row, fetch, ctx, learned)
            stats[key] = stats.get(key, 0) + 1
            stats["processed"] += 1
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — une fiche en échec n'arrête jamais les autres
            conn.rollback()
            if store.mark_error(conn, row["id"], errors.classify(exc), f"IMPORTED: {exc}") is not None:
                db.execute(conn, "UPDATE local_prospects SET pipeline_stage='IMPORTED' WHERE id=%s", (row["id"],))   # reprise après le délai
            conn.commit()
            stats["errors"] += 1
            log.warning("[local] fiche importée #%s non vérifiée : %s", row["id"], exc)
    return stats


def main() -> None:
    with db.connect() as conn:
        log.info("[local] fiches importées : %s", run(conn))


if __name__ == "__main__":
    main()
