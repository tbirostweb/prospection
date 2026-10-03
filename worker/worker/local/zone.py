"""Zone d'une campagne : géocodage d'une commune (API officielle geo.api.gouv.fr — jamais Nominatim) et distances."""
from __future__ import annotations

import math

from . import http

GEO_API = "https://geo.api.gouv.fr/communes"
EARTH_KM = 6371.0088


def _fold(s: str | None) -> str:
    from .textmatch import fold
    return fold(s)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def geocode_commune(city: str = "", postal_code: str = "", conn=None, department: str = "") -> dict | None:
    """{'city', 'commune_code', 'postal_code', 'department', 'latitude', 'longitude', 'population'} ou None si introuvable.
    Une commune homonyme est départagée par le code postal ou le département. Département seul : centre = sa commune la plus peuplée."""
    department = (department or "").strip().upper()
    if not city and not postal_code and not department:
        return None
    params = {"fields": "nom,code,codesPostaux,centre,departement,population", "boost": "population", "limit": 5}
    if postal_code:
        params["codePostal"] = postal_code
    if city:
        params["nom"] = city
    if department:
        params["codeDepartement"] = department
    data = http.get_json(GEO_API, params, conn=conn, cache_hours=24 * 30)
    if not isinstance(data, list) or not data:
        return None
    c = data[0]
    exact = [x for x in data if _fold(x.get("nom")) == _fold(city)] if city else data[:1]
    # Commune homonyme (Saint-Denis : 93 ou La Réunion…) sans code postal ni département : choisir « la plus peuplée » serait une supposition silencieuse.
    ambiguous = bool(city and not postal_code and not department and len({x["departement"]["code"] for x in exact if x.get("departement")}) > 1)
    alternatives = [f"{x['nom']} ({(x.get('departement') or {}).get('code')}, {(x.get('codesPostaux') or [''])[0]})" for x in exact[:5]] if ambiguous else []
    lon, lat = (c.get("centre") or {}).get("coordinates") or (None, None)
    if lat is None:
        return None
    return {"city": c["nom"], "commune_code": c["code"], "department": (c.get("departement") or {}).get("code"),
            "postal_code": postal_code or (c.get("codesPostaux") or [""])[0], "latitude": round(lat, 6),
            "longitude": round(lon, 6), "population": c.get("population"),
            "ambiguous": ambiguous, "alternatives": alternatives, "confidence": 0.5 if ambiguous else 0.95}
