"""Exécution d'une campagne de prospection locale, par étapes REPRENABLES, IDEMPOTENTES et bornées (temps, requêtes, coût).

    python -m worker.local.run            # traite les campagnes « queued » / « running » (cron toutes les 15 min)

Étapes d'un prospect (`pipeline_stage`) : DISCOVERED → ENRICHING (OSM) → WEBSITE_SEARCH → WEBSITE_VALIDATION → [AUDIT si LOCAL_AUDIT=1] →
CONTACT_DISCOVERY → SCORING → DONE ; en cas d'échec : ERROR (catégorie, tentatives, `next_retry_at` avec backoff). Chaque étape lit ce qui est
déjà enregistré : un crash, un redémarrage Docker ou une base momentanément indisponible ne fait rien retraiter inutilement.

L'essentiel par entreprise : a-t-elle un site (officiel, vérifié) ? et un moyen de la contacter. Le reste (Lighthouse, approfondissement,
revérifications, BODACC par SIREN) n'est plus fait ici : le débit va aux fiches nouvelles. L'audit passif est désactivé par défaut (LOCAL_AUDIT).

Règles :
  * un prospect en échec n'arrête JAMAIS la campagne ; une recherche indisponible (moteurs en cooldown) met la campagne en attente, sans rien
    conclure sur les prospects (ce n'est pas « aucun site ») : les fiches qui ont besoin d'une recherche attendent le passage suivant ;
  * la couverture de la découverte (pages, résultats disponibles, limites atteintes) est enregistrée : une campagne partielle est marquée telle ;
  * Ollama n'est pas utilisé ici : tout vient du code (règles et mesures).
"""
from __future__ import annotations

import json
import os
import time

from .. import db
from ..config import log
from . import audit as audit_mod
from . import (chains, contacts, engines, snippets, socials as socials_mod, errors, fetch as fetch_mod, identity, naf as naf_mod, osm, scoring, sirene,
               sitefinder, store, zone)
from .errors import SearchUnavailable

TIME_BUDGET_S = int(os.getenv("LOCAL_TIME_BUDGET_S") or "600")
LOOKUPS_PER_RUN = int(os.getenv("LOCAL_LOOKUPS_PER_RUN") or "30")
AUDIT = os.getenv("LOCAL_AUDIT", "0") == "1"    # audit passif du site (SEO, technique, modernisation) : désactivé par défaut
NAF_CHUNK = 10
SITE_STATES = "('CONFIRMED','PROBABLE')"


def _campaign_prospects(conn, cid: int, where: str = "", params: tuple = (), limit: int | None = None, order: str = "p.prospect_score DESC, p.id") -> list[dict]:
    sql = f"""SELECT p.* FROM local_prospects p JOIN local_prospect_campaigns c ON c.prospect_id = p.id
              WHERE c.campaign_id = %s {where} ORDER BY {order}""" + (f" LIMIT {int(limit)}" if limit else "")
    return db.fetch_all(conn, sql, (cid, *params))


class Ctx:
    """Réglages chargés une fois par passage."""

    def __init__(self, conn, camp: dict):
        self.settings = store.load_settings(conn)
        self.catalog = naf_mod.load_catalog(self.settings.get("local_activities"))
        naf_mod.set_extra_brands(self.settings.get("local_banned_brands"))
        self.weights, self.thresholds = self.settings.get("local_weights"), self.settings.get("local_thresholds")
        opts = self.settings.get("local_options") or {}
        self.site_thresholds = sitefinder.Thresholds.from_options(opts)
        self.category_weights = {str(k): float(v) for k, v in (opts.get("category_weights") or {}).items() if isinstance(v, (int, float))}
        self.radius = float(camp["radius_km"])
        self.osm_places: list | None = None
        self.learning = self.settings.get("local_learning")
        self.focus = opts.get("focus") or scoring.DEFAULT_FOCUS                # priorité de ciblage : refonte (défaut), sans site, équilibré            # ce que tes résultats ont appris (learning.refresh)
        self.dns = lambda domains: sitefinder.dns_resolves(domains)    # domaines devinés d'après le nom (résolus à l'appel : remplaçable en test)


def _rescore(conn, row: dict, ctx: Ctx, issues: list | None = None) -> dict:
    p = store.row_for_scoring(row)
    key, _l, web = naf_mod.activity_of(p.get("naf_code"), ctx.catalog)
    s = scoring.score_prospect(p, ctx.weights, ctx.thresholds, web, ctx.radius, category_weight=ctx.category_weights.get(key or "", naf_mod.default_weight(key)),
                               learning=ctx.learning, focus=ctx.focus)
    store.save_score(conn, row["id"], s)
    return s


# ── Découverte ───────────────────────────────────────────────────────────────────────────────────────────
def discover(conn, camp: dict, ctx: Ctx, stats: dict) -> dict:
    codes, unknown = naf_mod.resolve_activities(json.loads(camp["activities"]) if isinstance(camp["activities"], str) else camp["activities"], ctx.catalog)
    if not codes:
        raise ValueError(f"aucune activité exploitable (ignorées : {unknown})")
    lat, lon, radius = float(camp["latitude"]), float(camp["longitude"]), float(camp["radius_km"])

    def independent(e) -> bool:
        return not naf_mod.chain_signals(e.name, e.trade_name, e.company_size, e.employee_range, e.establishments_open, e.legal_category, e.naf_code) \
            and not naf_mod.excluded_reason(e.naf_code, f"{e.trade_name or ''} {e.name}", legal_category=e.legal_category)

    cov = {"pages": 0, "units_available": 0, "imported": 0, "kept": 0, "limit_hit": False, "stopped": "exhausted", "chunks": 0, "unknown_activities": unknown}
    per_chunk = max(1, int(camp["max_companies"]) // max(1, -(-len(codes) // NAF_CHUNK)))
    for i in range(0, len(codes), NAF_CHUNK):
        chunk_cov: dict = {}
        for est in sirene.discover(lat, lon, radius, codes[i:i + NAF_CHUNK], per_chunk, conn=conn, accept=independent, coverage=chunk_cov):
            if not independent(est):
                # chaîne, franchise, grosse entreprise ou agence web : BANNIE dès la découverte (jamais importée ni recherchée)
                stats["banned"] = stats.get("banned", 0) + 1
                continue
            if store.other_establishment(conn, est.siren, est.siret):
                # UNE fiche par entreprise : un 2e établissement (même SIREN, même patron, même site) ne ferait qu'un doublon à contacter
                stats["same_company"] = stats.get("same_company", 0) + 1
                continue
            _pid, created = store.upsert_establishment(conn, est, camp["id"], ctx.catalog)
            stats["discovered"] = stats.get("discovered", 0) + 1
            stats["new"] = stats.get("new", 0) + int(created)
        conn.commit()
        cov["chunks"] += 1
        for k in ("pages", "units_available", "imported", "kept"):
            cov[k] += chunk_cov.get(k, 0)
        if chunk_cov.get("stopped") in ("company_limit", "page_limit"):
            cov["limit_hit"] = True
            cov["stopped"] = chunk_cov["stopped"]
        cov["radius_capped"] = cov.get("radius_capped") or chunk_cov.get("radius_capped", False)
    cov["partial"] = bool(cov["limit_hit"] or cov.get("radius_capped"))
    stats["discovery_done"] = True
    return cov


def enrich_osm(conn, camp: dict, ctx: Ctx, stats: dict) -> None:
    """OSM en complément : UNE requête Overpass par campagne (cache 7 j), correspondance par proximité + nom. Jamais bloquant, jamais une vérité."""
    if not osm.ENABLED or stats.get("osm_done"):
        return
    try:
        places = osm.fetch_places(float(camp["latitude"]), float(camp["longitude"]), ctx.radius, conn)
    except errors.LocalError as exc:
        stats["osm_error"] = str(exc)[:120]
        errors.record(conn, exc.category, "osm", campaign_id=camp["id"], detail=exc.detail)
        log.warning("[local] OSM indisponible : %s", exc)
        return
    n = 0
    for r in _campaign_prospects(conn, camp["id"], "AND p.osm_data IS NULL"):
        m = osm.match(r, places)
        if m:
            store.save_osm(conn, r["id"], m)
            n += 1
        elif not r.get("is_chain") and (brand := osm.brand_at_address(r, places)):
            # même adresse qu'une enseigne de chaîne du même métier : la raison sociale est celle du FRANCHISÉ (constaté : « M.B.E.SUB » = un Subway)
            db.execute(conn, """UPDATE local_prospects SET is_chain=1, chain_kind='FRANCHISE', chain_confidence=0.85, chain_name=%s,
                                chain_evidence=%s, prospect_score=0, category='IGNORER' WHERE id=%s""", (brand["brand"], json.dumps([{"label": brand["evidence"], "weight": 0.85}], ensure_ascii=False), r["id"]))
            stats["osm_franchises"] = stats.get("osm_franchises", 0) + 1
    stats.update(osm_done=True, osm_places=len(places), osm_matched=n, osm_truncated=len(places) >= osm.MAX_ELEMENTS)
    if len(places) >= osm.MAX_ELEMENTS:
        log.warning("[local] OSM : réponse plafonnée à %d objets — couverture partielle dans cette zone dense", osm.MAX_ELEMENTS)
    conn.commit()


# ── Un prospect ──────────────────────────────────────────────────────────────────────────────────────────
def _company(conn, row: dict) -> dict:
    company = {k: row.get(k) for k in ("siret", "siren", "company_name", "trade_name", "city", "postal_code", "address", "phone", "activity_label")}
    osm_data = row.get("osm_data")
    osm_data = json.loads(osm_data) if isinstance(osm_data, (str, bytes)) else osm_data
    if osm_data and osm_data.get("phone") and not company.get("phone"):
        company["phone"] = osm_data["phone"]                    # sert UNIQUEMENT à chercher / vérifier (jamais affiché comme contact confirmé)
    company["bad_domains"] = store.bad_domains(conn, row.get("siret"), row.get("fingerprint"))
    return company


def _known_urls(conn, row: dict) -> list[str]:
    urls: list[str] = []
    fb = db.fetch_one(conn, "SELECT url FROM local_site_feedback WHERE prospect_id=%s AND kind='found_site' AND processed_at IS NULL ORDER BY id DESC LIMIT 1", (row["id"],))
    if fb and fb["url"]:
        urls.append(fb["url"])
    osm_data = row.get("osm_data")
    osm_data = json.loads(osm_data) if isinstance(osm_data, (str, bytes)) else osm_data
    if osm.SITE_HINT and osm_data and osm_data.get("website") and float(osm_data.get("confidence") or 0) >= 0.5:
        w = osm_data["website"]
        urls.append(w if w.startswith("http") else "https://" + w)
    return urls


def _local_presence(a, res, row: dict) -> dict:
    lp = dict(a.local_presence or {})
    city = (row.get("city") or "").lower()
    lp["title_has_locality"] = bool(city and city in (a.title or "").lower())
    lp["address_consistent"] = any(e.get("code") in ("address_exact", "address_street") for e in res.evidence)
    lp["found_at_strategy"] = len(res.strategies_tried) if res.accepted else None
    lp["contact_page_found"] = bool(res.pages and htmlinfo_has_contact(res.pages))
    lp["signals_present"] = sum(bool(lp.get(k)) for k in ("local_business_schema", "title_has_locality", "address_consistent", "contact_page_found"))
    lp["signals_total"] = 4
    return lp


def htmlinfo_has_contact(pages) -> bool:
    from . import htmlinfo
    return bool(htmlinfo.internal_links(pages[0], contacts.CONTACT_LINKS)) if pages else False


def _osm_contacts(row: dict, c: dict) -> dict:
    """Complète les coordonnées avec OSM (source signalée, confiance moyenne) SEULEMENT si le site n'en a pas donné."""
    osm_data = row.get("osm_data")
    osm_data = json.loads(osm_data) if isinstance(osm_data, (str, bytes)) else osm_data
    if not osm_data:
        return c
    if not c.get("phone") and osm_data.get("phone"):
        from . import htmlinfo
        n = htmlinfo.normalize_phone(osm_data["phone"])
        if n:
            c["phone"], c["phone_confidence"] = n, 0.55
            c["evidence"].append({"kind": "phone", "value": n, "via": "osm", "source": "OpenStreetMap", "url": None, "confidence": 0.55})
    if not c.get("email") and osm_data.get("email"):
        c["email"], c["email_kind"] = osm_data["email"].lower(), "UNCERTAIN"
        c["evidence"].append({"kind": "email", "value": c["email"], "category": "UNCERTAIN", "via": "osm", "source": "OpenStreetMap", "url": None, "confidence": 0.5})
    confs = [e["confidence"] for e in c["evidence"] if e["kind"] in ("phone", "email", "form")]
    if confs:
        c["contact_confidence"] = round(min(0.98, max(confs) + (0.04 if len(confs) > 1 else 0.0)), 2)
    return c


def _self_description(home) -> str:
    """Ce que le site dit de l'entreprise (titre, description, titres H1) — JAMAIS le texte entier : les mentions légales de presque
    tous les sites citent un « hébergeur » et un « développement web » (le prestataire), constaté en réel sur des plombiers de Troyes."""
    return " ".join([home.title or "", home.meta.get("description") or "", home.meta.get("og:description") or "", *(home.h1 or [])])


def _search_down(exc: SearchUnavailable):
    """Recherche coupée pour la fin du passage : lève aussitôt, sans solliciter de nouveau des moteurs déjà saturés."""
    def down(_query: str):
        raise SearchUnavailable(str(exc), exc.category)
    return down


def process_prospect(conn, row: dict, ctx: Ctx, stats: dict, search, fetch) -> None:
    """Fait UNIQUEMENT ce qui reste à faire pour ce prospect (voir la docstring du module)."""
    pid = row["id"]
    issues = None
    pages = None
    res = None
    status = row.get("website_status")
    if status is None:
        store.set_stage(conn, pid, "WEBSITE_SEARCH")
        res = sitefinder.resolve(_company(conn, row), search, fetch, ctx.site_thresholds, known_urls=_known_urls(conn, row), dns=ctx.dns)
        store.set_stage(conn, pid, "WEBSITE_VALIDATION")
        found = socials_mod.from_hits({**_company(conn, row), "manager_name": row.get("manager_name")}, res.hits)   # jamais un profil non attribué
        store.save_resolution(conn, pid, res, socials=[x["url"] for x in found])
        store.save_socials(conn, pid, found)
        fb = db.fetch_one(conn, "SELECT id FROM local_site_feedback WHERE prospect_id=%s AND kind='found_site' AND processed_at IS NULL ORDER BY id DESC LIMIT 1", (pid,))
        if fb:
            _analyse_found_site(conn, fb["id"], row, res)
        key = {"CONFIRMED": "confirmed", "PROBABLE": "probable", "NOT_FOUND": "not_found", "UNCERTAIN": "uncertain", "UNREACHABLE": "unreachable"}[res.status]
        stats[key] = stats.get(key, 0) + 1
        stats["searches"] = stats.get("searches", 0) + len(res.strategies_tried)
        status, pages = res.status, (res.pages or None)
        row = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    if status in ("CONFIRMED", "PROBABLE"):
        url = row["website_url"]
        if pages is None:                                       # reprise après crash : accueil + pages utiles relus
            pages, _home = sitefinder._load_pages(url, fetch, _company(conn, row))
            res = None
            if not pages:                                       # le site ne répond pas à cet instant : à reprendre (backoff), ni « audité » ni « sans contact »
                raise errors.LocalError(errors.SITE_TIMEOUT, f"accueil illisible à la reprise : {url}")
        if store.is_do_not_contact(conn, domain=row.get("canonical_domain")):
            db.execute(conn, "UPDATE local_prospects SET do_not_contact=1, status='DO_NOT_CONTACT' WHERE id=%s", (pid,))
        if pages and not row.get("excluded_reason") and (reason := naf_mod.excluded_reason(None, None, _self_description(pages[0]))):
            db.execute(conn, "UPDATE local_prospects SET excluded_reason=%s WHERE id=%s", (reason, pid))
        if AUDIT and row.get("audited_at") is None:            # désactivé par défaut : todo_sql() n'exige alors pas `audited_at`
            store.set_stage(conn, pid, "AUDIT")
            a = audit_mod.audit_site(url, fetch, home=pages[0] if pages else None)
            if a.response_ms is None:
                probe = fetch(url)
                a.response_ms = getattr(probe, "elapsed_ms", None) if probe is not None else None
                a.performance_score = audit_mod._response_score(a.response_ms, a.html_bytes)
                a.performance_status = "LIGHT" if a.response_ms is not None else "UNKNOWN"
            a.local_presence = _local_presence(a, res, row) if res else a.local_presence
            store.save_audit(conn, pid, a)
            stats["audited"] = stats.get("audited", 0) + 1
            issues = [i.__dict__ for i in a.issues]
        prov = row.get("provenance")
        prov = json.loads(prov) if isinstance(prov, (str, bytes)) else (prov or {})
        if "contacts" not in prov:
            store.set_stage(conn, pid, "CONTACT_DISCOVERY")
            site_domain = row.get("canonical_domain") or (pages[0].domain if pages else "")
            pages = contacts.discover(pages or [], fetch, site_domain)
            c = _osm_contacts(row, contacts.extract(pages, site_domain))
            if res is not None and not (c["phone"] and c["email"]):
                c = _merge(c, snippets.extract({**_company(conn, row), "department": row.get("department")}, res.hits))
            store.save_contacts(conn, pid, c)
            store.set_provenance(conn, pid, "contacts", {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "found": bool(c["phone"] or c["email"] or c["contact_form"])})
            if not (c["phone"] or c["email"] or c["contact_form"] or c["contact_page"]):
                errors.record(conn, errors.CONTACT_NOT_FOUND, "site", prospect_id=pid, detail=site_domain)
            stats["contacts"] = stats.get("contacts", 0) + int(bool(c["phone"] or c["email"] or c["contact_form"]))
        if pages:                                               # profils revendiqués par le site officiel lui-même
            known = row.get("social_links")
            known = json.loads(known) if isinstance(known, (str, bytes)) else (known or [])
            store.save_socials(conn, pid, socials_mod.merge(known, socials_mod.from_pages(pages)))
        # chaîne / réseau / franchise : d'après le site + la base
        fresh = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
        ident = res.identity if res else identity.extract(pages or [])
        ch = chains.assess(fresh, pages, ident, **store.chain_context(conn, fresh))
        if ch.confidence >= (float(fresh.get("chain_confidence") or 0)) or ch.kind:
            store.save_chain(conn, pid, ch)
            if ch.kind:
                stats["chains"] = stats.get("chains", 0) + 1
    elif status == "UNREACHABLE":
        issues = [{"code": "unreachable", "label": "Site inaccessible", "severity": "high", "category": "availability", "evidence": ""}]
        _fallback_contacts(conn, pid, row, res, search)
    elif status in ("NOT_FOUND", "UNCERTAIN"):
        # Aucun site confirmé : sans contact, un prospect reste plafonné sous le seuil « À contacter ». Sources encore possibles : OpenStreetMap
        # (déjà vérifié par proximité + nom) et les extraits de recherche attribuables (nom + ville) — jamais un contact inventé.
        _fallback_contacts(conn, pid, row, res, search)
    store.set_stage(conn, pid, "SCORING")
    fresh = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    _rescore(conn, fresh, ctx, issues)
    store.refresh_siblings(conn, fresh.get("siren"))
    store.clear_error(conn, pid, "DONE")
    # plus d'enrichissement approfondi : une fiche traitée est PRÊTE (today.READY / READY_SQL de web/lib/local.ts exigent deep_enriched_at)
    db.execute(conn, "UPDATE local_prospects SET deep_enriched_at=COALESCE(deep_enriched_at, UTC_TIMESTAMP()) WHERE id=%s", (pid,))


def _merge(c: dict, extra: dict) -> dict:
    """Complète `c` avec `extra` champ par champ, sans jamais remplacer une coordonnée déjà trouvée par une source plus fiable."""
    for kind in ("phone", "email"):
        if not c.get(kind) and extra.get(kind):
            c[kind] = extra[kind]
            if kind == "phone":
                c["phone_confidence"] = extra.get("phone_confidence")
            else:
                c["email_kind"] = extra.get("email_kind")
            c["contact_source_url"] = c.get("contact_source_url") or extra.get("contact_source_url")
            c["evidence"] += [e for e in extra["evidence"] if e["kind"] == kind]
    confs = [e["confidence"] for e in c["evidence"] if e["kind"] in ("phone", "email", "form")]
    if confs:
        c["contact_confidence"] = round(min(0.98, max(confs) + (0.04 if len(confs) > 1 else 0.0)), 2)
    return c


def _fallback_contacts(conn, pid: int, row: dict, res, search) -> None:
    """Prospect sans site confirmé : OSM, puis extraits de recherche (ceux de la résolution, ou UNE requête dédiée pour un prospect déjà traité)."""
    prov = row.get("provenance")
    prov = json.loads(prov) if isinstance(prov, (str, bytes)) else (prov or {})
    if "contacts" in prov:
        return
    c = _osm_contacts(row, {"phone": None, "phone_confidence": None, "email": None, "email_kind": None, "contact_form": False, "contact_page": None,
                            "contact_source_url": None, "contact_confidence": None, "evidence": []})
    company = {**_company(conn, row), "department": row.get("department")}
    if not (c["phone"] and c["email"]):
        hits = res.hits if res is not None else None
        if hits is None and search is not None and (q := snippets.contact_query(company)):
            hits = search(q) or []                          # SearchUnavailable remonte : le prospect est repris plus tard, rien n'est conclu
        c = _merge(c, snippets.extract(company, hits or []))
    store.save_contacts(conn, pid, c)
    store.set_provenance(conn, pid, "contacts", {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "found": bool(c["phone"] or c["email"]),
                                                 "via": sorted({e["via"] for e in c["evidence"]})})


def _analyse_found_site(conn, feedback_id: int, row: dict, res) -> None:
    """« J'ai trouvé le site » : pourquoi la résolution automatique l'avait-elle manqué ? (analyse stockée, aucun apprentissage global)."""
    given = db.fetch_one(conn, "SELECT url FROM local_site_feedback WHERE id=%s", (feedback_id,))["url"]
    dom = sitefinder.canonical_domain(given or "")
    cand = next((c for c in res.candidates if c.get("domain") == dom), None)
    seen = res.search_domains.get(dom)
    if seen is None:
        why = "le site n'est apparu dans aucune des recherches essayées : à améliorer côté requêtes (nom d'usage, enseigne) ou moteurs"
    elif seen != "candidat":
        why = f"le site est apparu dans les résultats mais a été écarté ({seen})"
    else:
        why = "le site est apparu comme candidat" + (f" mais sa confiance ({cand['confidence']}) était insuffisante" if cand and cand["confidence"] < 0.8 else "")
    analysis = {"why": why, "in_search_results": seen, "strategies": res.strategies_tried, "queries": res.queries, "status_now": res.status,
                "confidence_now": cand["confidence"] if cand else None, "evidence": cand["evidence"] if cand else None}
    db.execute(conn, "UPDATE local_site_feedback SET analysis=%s, processed_at=UTC_TIMESTAMP() WHERE id=%s", (json.dumps(analysis, ensure_ascii=False, default=str), feedback_id))


def _handle_failure(conn, camp_id: int, row: dict, exc: Exception, stats: dict) -> None:
    cat = errors.classify(exc)
    delay = store.mark_error(conn, row["id"], cat, f"{row.get('pipeline_stage') or ''}: {exc}")
    errors.record(conn, cat, "runner", campaign_id=camp_id, prospect_id=row["id"], detail=str(exc))
    stats["errors"] = stats.get("errors", 0) + 1
    if delay is None:
        stats["abandoned"] = stats.get("abandoned", 0) + 1
    log.warning("[local] prospect #%s en erreur (%s), reprise %s", row["id"], cat, f"dans {delay} min" if delay else "abandonnée")


# ── Fiches à traiter ─────────────────────────────────────────────────────────────────────────────────────
RETRY_DUE = "(p.pipeline_stage <> 'ERROR' OR (p.next_retry_at IS NOT NULL AND p.next_retry_at <= UTC_TIMESTAMP()))"
RETRY_PLANNED = "(p.pipeline_stage <> 'ERROR' OR p.next_retry_at IS NOT NULL)"
NO_SITE_STATES = ("NOT_FOUND", "UNCERTAIN", "UNREACHABLE")


def todo_sql(retry: str = RETRY_DUE) -> str:
    """Ce qui reste à faire. L'audit n'est exigé que s'il est activé (LOCAL_AUDIT=1) : sinon une fiche avec site est terminée dès que ses
    contacts ont été cherchés (exiger `audited_at` sans jamais l'écrire ferait retraiter la même fiche à chaque passage)."""
    site_todo = "(p.audited_at IS NULL OR JSON_EXTRACT(p.provenance, '$.contacts') IS NULL)" if AUDIT else "JSON_EXTRACT(p.provenance, '$.contacts') IS NULL"
    return f"""AND p.excluded_reason IS NULL AND p.is_chain = 0 AND p.do_not_contact = 0
   AND {retry}
   AND (p.website_status IS NULL
        OR (p.website_status IN {SITE_STATES} AND {site_todo})
        OR (p.website_status IN ('NOT_FOUND','UNCERTAIN','UNREACHABLE') AND JSON_EXTRACT(p.provenance, '$.contacts') IS NULL))"""


def _needs_search(row: dict) -> bool:
    """Fiche dont le traitement passe par les moteurs : site encore inconnu (résolution) ou sans site et sans contacts cherchés (requête de
    contact). Une fiche au site identifié n'en a pas besoin (contacts lus sur le site)."""
    return row.get("website_status") is None or row.get("website_status") in NO_SITE_STATES


def run_campaign(conn, camp: dict, search=None, fetch=None, deadline: float | None = None) -> dict:
    t_start = time.monotonic()
    ctx = Ctx(conn, camp)
    stats = json.loads(camp["stats"]) if isinstance(camp.get("stats"), (str, bytes)) else dict(camp.get("stats") or {})
    deadline = deadline or time.monotonic() + TIME_BUDGET_S
    cid = camp["id"]
    fetcher = fetch_mod.Fetcher() if fetch is None else None
    fetch = fetch or fetcher
    run = {"processed": 0, "confirmed": 0, "probable": 0, "uncertain": 0, "not_found": 0, "unreachable": 0, "errors": 0, "searches": 0}
    db.execute(conn, "UPDATE local_campaigns SET status='running', last_run_at=UTC_TIMESTAMP(), last_error=NULL WHERE id=%s", (cid,))
    conn.commit()
    pool = None
    try:
        if camp.get("latitude") is None:
            z = zone.geocode_commune("" if camp.get("city", "").startswith("Département ") else camp["city"], camp.get("postal_code") or "", conn,
                                     department=camp.get("department") or "")
            if not z:
                raise ValueError(f"commune introuvable : {camp['city']} {camp.get('postal_code') or ''}")
            if z.get("ambiguous") and not camp.get("department"):
                errors.record(conn, errors.GEO_FAILED, "geo", campaign_id=cid, detail="commune ambiguë : " + ", ".join(z["alternatives"]))
                raise ValueError("commune ambiguë : précisez le code postal ou le département — " + ", ".join(z["alternatives"]))
            db.execute(conn, "UPDATE local_campaigns SET latitude=%s, longitude=%s, department=%s, commune_code=%s, postal_code=COALESCE(postal_code,%s) WHERE id=%s",
                       (z["latitude"], z["longitude"], z["department"], z["commune_code"], z["postal_code"], cid))
            camp = {**camp, "latitude": z["latitude"], "longitude": z["longitude"]}
        cov = json.loads(camp["coverage"]) if isinstance(camp.get("coverage"), (str, bytes)) else camp.get("coverage")
        if not stats.get("discovery_done"):
            cov = discover(conn, camp, ctx, stats)
            db.execute(conn, "UPDATE local_campaigns SET coverage=%s WHERE id=%s", (json.dumps(cov), cid))
            conn.commit()
        enrich_osm(conn, camp, ctx, stats)
        stats.pop("time_budget_hit", None)
        for r in _campaign_prospects(conn, cid, "AND p.scored_at IS NULL"):
            if time.monotonic() > deadline:                                            # le reste sera scoré au passage suivant (scored_at NULL)
                stats["time_budget_hit"] = True
                break
            _rescore(conn, r, ctx)                                                     # score PRÉLIMINAIRE (site non recherché)
        # fiches traitées avant la suppression de l'enrichissement approfondi : prêtes elles aussi (sinon jamais proposées)
        db.execute(conn, """UPDATE local_prospects p JOIN local_prospect_campaigns c ON c.prospect_id=p.id SET p.deep_enriched_at=UTC_TIMESTAMP()
                            WHERE c.campaign_id=%s AND p.pipeline_stage='DONE' AND p.deep_enriched_at IS NULL""", (cid,))
        conn.commit()

        pool = search or engines.SearchPool(conn)
        searcher = pool                                    # remplacé par un « moteur coupé » après une SearchUnavailable (pool garde ses métriques)
        # jamais traités d'abord : des fiches en attente de moteur (recherche indisponible) ne doivent pas bloquer les nouvelles
        todo = [] if stats.get("time_budget_hit") else _campaign_prospects(conn, cid, todo_sql(), limit=LOOKUPS_PER_RUN,
                                                                            order="p.pipeline_stage <> 'DISCOVERED', p.prospect_score DESC, p.id")
        stats.pop("waiting", None)
        stats.pop("resume_at", None)
        search_down = False
        skipped = 0
        for row in todo:
            if time.monotonic() > deadline:
                stats["time_budget_hit"] = True
                break
            if search_down and _needs_search(row):
                # moteurs indisponibles : rien n'est tenté (ni DNS, ni lecture de domaines devinés, ni écriture) ; la fiche reste telle quelle
                # dans TODO et sera reprise au passage suivant, en tête (pipeline_stage inchangé)
                skipped += 1
                continue
            try:
                process_prospect(conn, row, ctx, stats, searcher, fetch)
                run["processed"] += 1
            except SearchUnavailable as exc:
                # Moteurs en cooldown / SearXNG injoignable : on n'écrit RIEN sur ce prospect (ce n'est pas « aucun site »), il sera repris.
                # La campagne continue seulement pour les fiches qui n'ont pas besoin des moteurs (site déjà identifié).
                conn.rollback()
                if not search_down:
                    search_down = True
                    errors.record(conn, exc.category, "search", campaign_id=cid, prospect_id=row["id"], detail=str(exc))
                    log.warning("[local] recherche indisponible (%s) : fiches à rechercher remises au passage suivant", exc)
                    stats["search_errors"] = stats.get("search_errors", 0) + 1
                    stats["waiting"] = str(exc)[:160]
                    stats["resume_at"] = engines.next_available_at(conn, getattr(pool, "names", None))
                    searcher = _search_down(exc)
                skipped += 1
                conn.commit()
                continue
            except Exception as exc:  # noqa: BLE001 — un prospect en échec n'arrête jamais la campagne
                conn.rollback()
                _handle_failure(conn, cid, row, exc, stats)
            if fetcher:
                for cat, url in fetcher.drain():
                    errors.record(conn, cat, "site", campaign_id=cid, prospect_id=row["id"], detail=url[:200])
            conn.commit()
        stats["search_skipped"] = skipped
        remaining = db.fetch_one(conn, f"""SELECT COUNT(*) AS n FROM local_prospects p JOIN local_prospect_campaigns c ON c.prospect_id=p.id
                                          WHERE c.campaign_id=%s {todo_sql(RETRY_PLANNED)}""", (cid,))["n"]
        stats["remaining"] = remaining
        finished = remaining == 0 and not stats.get("waiting")
        db.execute(conn, "UPDATE local_campaigns SET status=%s, last_finished_at=IF(%s, UTC_TIMESTAMP(), last_finished_at), stats=%s, last_error=%s WHERE id=%s",
                   ("done" if finished else "running", int(finished), json.dumps(stats), (stats.get("waiting") or None), cid))
    except Exception as exc:  # noqa: BLE001 — une campagne en erreur n'arrête pas les autres
        log.exception("[local] campagne #%s en erreur", cid)
        conn.rollback()
        errors.record(conn, errors.classify(exc), "campaign", campaign_id=cid, detail=str(exc))
        db.execute(conn, "UPDATE local_campaigns SET status='error', last_error=%s, stats=%s WHERE id=%s", (str(exc)[:250], json.dumps(stats), cid))
    _write_run_metrics(conn, cid, run, stats, pool, t_start)
    conn.commit()
    return stats


def _write_run_metrics(conn, cid: int, run: dict, stats: dict, pool, t_start: float) -> None:
    try:
        for k in ("confirmed", "probable", "uncertain", "not_found", "unreachable"):
            run[k] = stats.get(k, 0) - stats.get(f"_last_{k}", 0)
            stats[f"_last_{k}"] = stats.get(k, 0)
        q, empty = getattr(pool, "queries", 0), getattr(pool, "empty", 0)
        run["searches"] = len(q) if isinstance(q, (list, tuple)) else int(q or 0)
        empty = len(empty) if isinstance(empty, (list, tuple)) else int(empty or 0)
        db.execute(conn, """INSERT INTO local_run_metrics (campaign_id, duration_ms, processed, confirmed, probable, uncertain, not_found, unreachable, errors, searches, searches_empty)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                   (cid, int((time.monotonic() - t_start) * 1000), run["processed"], run["confirmed"], run["probable"], run["uncertain"], run["not_found"],
                    run["unreachable"], stats.get("errors", 0) - stats.get("_last_errors", 0), run["searches"], empty))
        stats["_last_errors"] = stats.get("errors", 0)
    except Exception as exc:  # noqa: BLE001 — les métriques ne doivent jamais casser une campagne
        log.warning("[local] métriques non écrites : %s", exc)


def run_pending() -> int:
    """Traite les campagnes en attente (une passe bornée). Renvoie le nombre de campagnes traitées."""
    with db.connect() as conn:
        try:
            from . import learning, rescore
            _model, changed = learning.refresh(conn)
            if changed:
                log.info("[local] nouveaux résultats appris : %d prospect(s) rescoré(s)", rescore.rescore_all(conn))
        except Exception as exc:  # noqa: BLE001 — l'apprentissage ne bloque jamais les campagnes
            conn.rollback()
            log.warning("[local] apprentissage non recalculé : %s", exc)
        camps = db.fetch_all(conn, "SELECT * FROM local_campaigns WHERE enabled=1 AND status IN ('queued','running') ORDER BY status='queued' DESC, id")
        deadline = time.monotonic() + TIME_BUDGET_S
        for camp in camps:
            if time.monotonic() > deadline:
                break
            run_campaign(conn, camp, deadline=deadline)
        try:
            from . import notify as local_notify
            local_notify.notify_new_businesses(conn)
            local_notify.notify_qualified(conn)
        except Exception as exc:  # noqa: BLE001 — une alerte qui échoue ne casse jamais le pipeline
            log.warning("[local] alertes Telegram en échec : %s", exc)
        return len(camps)


def _acquire_lock():
    """Verrou inter-processus propre à la prospection locale (indépendant de celui du pipeline d'opportunités)."""
    import fcntl
    path = os.getenv("LOCAL_LOCK", "/app/logs/local.lock")
    try:
        fh = open(path, "w")
    except OSError:
        return open(os.devnull, "w")              # dossier absent (poste de développement) : pas de protection, on continue
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main() -> None:
    lock = _acquire_lock()
    if lock is None:
        log.warning("[local] un passage de prospection locale est déjà en cours")
        return
    try:
        log.info("[local] %d campagne(s) traitée(s)", run_pending())
    finally:
        lock.close()


if __name__ == "__main__":
    main()
