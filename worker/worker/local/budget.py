"""Budget PROBABLE d'un prospect : peut-il payer un site ? Estimation prudente, expliquée, jamais une certitude.

Trois indices gratuits et officiels :
  * la VALEUR D'UN CLIENT dans son métier : un plombier, un paysagiste ou un architecte rembourse un site avec UN chantier ; un bar ou une
    boulangerie vit de petits paniers et achète rarement un site ;
  * le CHIFFRE D'AFFAIRES quand les comptes sont publiés (API Recherche d'entreprises, champ `finances` — souvent absent pour les TPE) ;
  * l'EFFECTIF (SIRENE) : 1 à 9 salariés = une vraie entreprise qui tourne, sans service marketing interne.
Résultat : un ajustement de -8 à +8 points sur le score brut, et un niveau « élevé / moyen / serré / inconnu ».
"""
from __future__ import annotations

# 1 = un client rapporte beaucoup (chantier, dossier, séjour) · 0,6 = moyen · 0,2 = petits paniers
CLIENT_VALUE: dict[str, float] = {
    "plombiers": 1.0, "electriciens": 1.0, "couvreurs": 1.0, "menuisiers": 1.0, "peintres": 0.8, "macons": 1.0, "paysagistes": 1.0,
    "immobilier": 1.0, "architectes": 1.0, "avocats": 0.9, "hebergements": 0.9, "demenageurs": 0.9, "auto_ecoles": 0.9, "garages": 0.8,
    "sante": 0.7, "veterinaires": 0.7, "opticiens": 0.7, "evenementiel": 0.8, "traiteurs": 0.8, "photographes": 0.7, "artisans_art": 0.7,
    "cours": 0.6, "fitness": 0.6, "beaute": 0.6, "coiffure": 0.5, "soins_personnels": 0.5, "restaurants": 0.6, "services": 0.6,
    "boutiques": 0.5, "fleuristes": 0.5, "commerces": 0.3, "boulangeries": 0.3, "bars": 0.3, "associations": 0.2,
}
MAX_POINTS = 8
SMALL_TEAM = {"01", "02", "03", "11"}          # 1-2, 3-5, 6-9, 10-19 salariés


def estimate(p: dict) -> dict:
    """{'points' (-8..+8), 'level', 'detail'} d'après `activity_key`, `revenue` (€), `revenue_year`, `employee_range`."""
    pts, notes = 0.0, []
    value = CLIENT_VALUE.get(p.get("activity_key") or "")
    if value is not None:
        pts += (value - 0.6) * 10                                   # +4 pour 1,0 · 0 pour 0,6 · -3 pour 0,3
        notes.append(f"valeur d'un client dans ce métier : {'élevée' if value >= 0.8 else 'moyenne' if value >= 0.5 else 'faible (petits paniers)'}")
    ca, year = p.get("revenue"), p.get("revenue_year")
    if ca is not None:
        ca = float(ca)
        k = f"{ca / 1000:,.0f} k€".replace(",", " ")
        if ca < 30_000:
            pts -= 5; notes.append(f"chiffre d'affaires {year} : {k} (budget serré)")
        elif ca < 100_000:
            notes.append(f"chiffre d'affaires {year} : {k}")
        elif ca < 2_000_000:
            pts += 4 if ca >= 300_000 else 2; notes.append(f"chiffre d'affaires {year} : {k} (de quoi investir)")
        else:
            notes.append(f"chiffre d'affaires {year} : {k} (structure importante)")
    if p.get("employee_range") in SMALL_TEAM:
        pts += 2; notes.append("équipe de quelques salariés : l'entreprise tourne")
    pts = max(-MAX_POINTS, min(MAX_POINTS, round(pts, 1)))
    known = value is not None or ca is not None
    level = "inconnu" if not known else "élevé" if pts >= 4 else "moyen" if pts >= 0 else "serré"
    return {"points": pts, "level": level, "detail": " · ".join(notes) or "aucun indice (métier hors catalogue, comptes non publiés)"}
