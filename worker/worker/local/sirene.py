"""Découverte d'entreprises : API « Recherche d'Entreprises » (données ouvertes SIRENE) + import de l'export officiel.

  * `near_point` : entreprises ayant un établissement dans un rayon (≤ 50 km) autour d'un point, filtrées par code(s) NAF ;
  * chaque résultat est une UNITÉ LÉGALE : ses établissements proches sont dans `matching_etablissements` (certains FERMÉS : filtrés ici) ;
  * seuls les établissements ACTIFS et à données DIFFUSABLES sont conservés (les données non diffusables ne sont jamais utilisées) ;
  * dédoublonnage par SIRET ; cadence ≤ 3 requêtes/s (limite officielle : 7/s).

Pour une zone très large ou plusieurs départements, préférer l'export bulk `StockEtablissement` (`import_stock_csv`) à des milliers de requêtes.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator

from . import http
from .zone import haversine_km

API = "https://recherche-entreprises.api.gouv.fr/near_point"
PER_PAGE = 25
MAX_RADIUS_KM = 50


@dataclass
class Establishment:
    siret: str
    siren: str
    name: str
    trade_name: str | None = None
    naf_code: str | None = None
    address: str | None = None
    city: str | None = None
    postal_code: str | None = None
    department: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = None
    created_at: date | None = None
    legal_category: str | None = None
    company_size: str | None = None
    employee_range: str | None = None
    establishments_open: int | None = None
    is_head_office: bool = False
    revenue: int | None = None               # chiffre d'affaires publié (€), souvent absent pour les TPE
    revenue_year: int | None = None
    manager_name: str | None = None          # dirigeant (registre officiel) : « Laure Crame » — jamais sa date de naissance
    commune_code: str | None = None          # code INSEE de la commune (export bulk : sert à situer l'établissement, faute de coordonnées)
    source: str = "sirene"
    raw_ref: str = field(default="", repr=False)


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def finances(unit: dict) -> tuple[int | None, int | None]:
    """(chiffre d'affaires, année) des derniers comptes publiés, ou (None, None)."""
    best = None
    for year, f in (unit.get("finances") or {}).items():
        ca = (f or {}).get("ca")
        if ca is not None and str(year).isdigit() and (best is None or int(year) > best[1]):
            best = (int(ca), int(year))
    return best or (None, None)


def manager(unit: dict) -> str | None:
    """Nom du dirigeant, prénom usuel d'abord : « Laure Crame ». Entrepreneur individuel : c'est la personne elle-même."""
    people = [d for d in unit.get("dirigeants") or [] if d.get("type_dirigeant") == "personne physique" and d.get("nom")]
    if not people:
        return None
    d = next((x for x in people if re.search(r"g[ée]rant|pr[ée]sident|exploitant", x.get("qualite") or "", re.I)), people[0])
    first = (d.get("prenoms") or "").split()[:1]
    return " ".join([*first, d["nom"]]).title()[:160] or None


def parse_unit(unit: dict, origin: tuple[float, float] | None = None, radius_km: float | None = None,
               naf_codes: Iterable[str] | None = None) -> list[Establishment]:
    """Établissements ACTIFS et diffusables d'une unité légale renvoyée par l'API (uniquement ceux dans le rayon, avec le bon NAF)."""
    wanted = {c.upper() for c in naf_codes or []}
    out: list[Establishment] = []
    etabs = unit.get("matching_etablissements") or [unit.get("siege") or {}]
    for e in etabs:
        if not e or e.get("etat_administratif") != "A" or e.get("statut_diffusion_etablissement", "O") != "O":
            continue                                                  # fermé ou non diffusable : jamais utilisé
        naf = e.get("activite_principale")
        if wanted and (naf or "").upper() not in wanted:
            continue
        lat, lon = _float(e.get("latitude")), _float(e.get("longitude"))
        dist = round(haversine_km(origin[0], origin[1], lat, lon), 1) if origin and lat is not None and lon is not None else None
        if dist is not None and radius_km is not None and dist > radius_km + 0.5:
            continue
        enseignes = e.get("liste_enseignes") or []
        out.append(Establishment(
            siret=e["siret"], siren=unit.get("siren") or e["siret"][:9], name=(unit.get("nom_complet") or unit.get("nom_raison_sociale") or "").strip(),
            trade_name=(enseignes[0] if enseignes else e.get("nom_commercial")) or None, naf_code=naf,
            address=e.get("adresse"), city=e.get("libelle_commune"), postal_code=e.get("code_postal"), department=e.get("departement"),
            latitude=lat, longitude=lon, distance_km=dist, created_at=_date(e.get("date_creation")),
            legal_category=str(unit.get("nature_juridique") or "") or None, company_size=unit.get("categorie_entreprise"),
            employee_range=unit.get("tranche_effectif_salarie"), establishments_open=unit.get("nombre_etablissements_ouverts"),
            is_head_office=bool(e.get("est_siege")), revenue=finances(unit)[0], revenue_year=finances(unit)[1], manager_name=manager(unit),
            raw_ref=e["siret"]))
    return out


SEARCH_API = "https://recherche-entreprises.api.gouv.fr/search"


def lookup_siren(siren: str, conn=None) -> dict | None:
    """Unité légale d'un SIREN (API officielle, cache 24 h) ou None. Sert à la veille : BODACC donne le SIREN d'une création, SIRENE donne
    l'établissement (adresse, coordonnées, NAF, état). `matching_etablissements` est vide pour une recherche par SIREN : `parse_unit` lit le siège."""
    data = http.get_json(SEARCH_API, {"q": siren, "per_page": 1}, conn=conn, cache_hours=24)
    for unit in (data or {}).get("results") or []:
        if str(unit.get("siren")) == siren:
            return unit
    return None


def search_page(lat: float, lon: float, radius_km: float, naf_codes: list[str], page: int = 1, conn=None) -> dict:
    params = {"lat": lat, "long": lon, "radius": min(radius_km, MAX_RADIUS_KM), "activite_principale": ",".join(naf_codes),
              "etat_administratif": "A", "per_page": PER_PAGE, "page": page}
    data = http.get_json(API, params, conn=conn, cache_hours=12)
    if not isinstance(data, dict) or "results" not in data:
        raise http.ApiError("recherche-entreprises : réponse inattendue")
    return data


def discover(lat: float, lon: float, radius_km: float, naf_codes: list[str], max_companies: int = 300,
             conn=None, accept=None, max_pages: int = 40, coverage: dict | None = None) -> Iterator[Establishment]:
    """Parcourt les pages de l'API. Un SIRET n'est rendu qu'une fois.

    L'API classe les GRANDES entreprises d'abord (constaté en réel : Buffalo Grill, Quick, Five Guys… en tête des restaurants de Troyes).
    Le plafond `max_companies` ne compte donc que les établissements que `accept(est)` retient (par défaut : tous) : on continue à
    descendre dans les pages tant que la quantité utile n'est pas atteinte, les chaînes étant quand même rendues (pour être marquées)."""
    seen: set[str] = set()
    kept = 0
    accept = accept or (lambda e: True)
    page = 1
    cov = coverage if coverage is not None else {}
    cov.setdefault("pages", 0)
    cov.setdefault("units_available", 0)
    cov.setdefault("imported", 0)
    cov.setdefault("kept", 0)
    cov.setdefault("radius_capped", radius_km > MAX_RADIUS_KM)
    cov["stopped"] = "exhausted"
    while kept < max_companies and page <= max_pages:
        data = search_page(lat, lon, radius_km, naf_codes, page, conn)
        cov["pages"] += 1
        cov["units_available"] += int(data.get("total_results") or 0) if page == 1 else 0
        units = data.get("results") or []
        if not units:
            return
        for unit in units:
            for est in parse_unit(unit, (lat, lon), radius_km, naf_codes):
                if est.siret in seen:
                    continue
                seen.add(est.siret)
                cov["imported"] += 1
                if accept(est):
                    kept += 1
                    cov["kept"] = kept
                yield est
                if kept >= max_companies:
                    cov["stopped"] = "company_limit"
                    return
        if page >= int(data.get("total_pages") or 1):
            return
        page += 1
    if page > max_pages:
        cov["stopped"] = "page_limit"


# ─── Export bulk officiel (StockEtablissement_utf8.csv) ─────────────────────────────────────────────────
def import_stock_csv(path: str, *, departments: Iterable[str] | None = None, postal_codes: Iterable[str] | None = None,
                     naf_codes: Iterable[str] | None = None, limit: int | None = None) -> Iterator[Establishment]:
    """Lit l'export officiel SIRENE (colonnes `StockEtablissement`) en flux, sans le charger en mémoire, et ne rend que les
    établissements ACTIFS, diffusables, dans les départements / codes postaux / NAF demandés. Pas de coordonnées dans ce fichier :
    `distance_km` reste None (à estimer ensuite depuis la commune)."""
    deps = {str(d) for d in departments or []}
    cps = {str(c) for c in postal_codes or []}
    nafs = {c.upper() for c in naf_codes or []}
    n = 0
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("etatAdministratifEtablissement") != "A" or row.get("statutDiffusionEtablissement", "O") != "O":
                continue
            cp = row.get("codePostalEtablissement") or ""
            if deps and not any(cp.startswith(d) for d in deps) and (row.get("codeCommuneEtablissement") or "")[:len(next(iter(deps)))] not in deps:
                continue
            if cps and cp not in cps:
                continue
            naf = (row.get("activitePrincipaleEtablissement") or "").upper()
            if nafs and naf not in nafs:
                continue
            street = " ".join(filter(None, [row.get("numeroVoieEtablissement"), row.get("typeVoieEtablissement"), row.get("libelleVoieEtablissement")]))
            yield Establishment(
                siret=row["siret"], siren=row["siren"], name=(row.get("denominationUsuelleEtablissement") or row.get("enseigne1Etablissement") or "").strip(),
                trade_name=row.get("enseigne1Etablissement") or None, naf_code=naf or None, address=f"{street} {cp} {row.get('libelleCommuneEtablissement') or ''}".strip(),
                city=row.get("libelleCommuneEtablissement"), postal_code=cp, department=cp[:2] if cp[:2] not in ("97", "98") else cp[:3],
                created_at=_date(row.get("dateCreationEtablissement")), employee_range=row.get("trancheEffectifsEtablissement") or None,
                is_head_office=row.get("etablissementSiege") == "true", commune_code=row.get("codeCommuneEtablissement") or None,
                source="sirene_bulk", raw_ref=row["siret"])
            n += 1
            if limit and n >= limit:
                return
