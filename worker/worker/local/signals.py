"""Signaux d'ACHAT : les moments où une entreprise commande un site — pas « a-t-elle besoin d'un site ? » mais « pourquoi MAINTENANT ? ».

Chaque signal vient d'une source officielle ou d'un constat vérifiable, jamais d'une supposition :
  takeover            reprise d'un commerce (BODACC « Ventes et cessions » / immatriculation suite à achat) : nouveau patron, tout est à refaire
  new_no_site         établissement ouvert il y a 1 à 6 mois (SIRENE) et aucun site trouvé : il cherche ses premiers clients
  rename              changement de nom / d'enseigne (BODACC) : l'ancien site, les cartes, la fiche Google deviennent faux
  move                déménagement (BODACC) : adresse, plan d'accès et horaires à changer partout
  new_establishment   nouvel établissement (BODACC) : une nouvelle adresse à faire connaître
  site_down           site identifié mais en panne (serveur ou domaine qui ne répond plus) : il perd des clients en ce moment
  site_diy            site sur un sous-domaine gratuit (Wix, Jimdo…) ou fourni par un annuaire (PagesJaunes / Solocal)
  socials_only        actif sur les réseaux sociaux mais sans site : il a des clients, pas de vitrine à leur montrer
  no_contact_way      site sans formulaire ni e-mail : les visiteurs ne peuvent pas écrire
Les signaux BODACC s'éteignent avec le temps (plein effet ≤ 6 mois, moitié jusqu'à 12 mois, rien au-delà).
"""
from __future__ import annotations

from datetime import date

SIGNALS: dict[str, tuple[str, float]] = {
    "takeover": ("Reprise d'un commerce (BODACC)", 1.0),
    "new_no_site": ("Ouvert récemment, sans site trouvé", 0.9),
    "rename": ("Changement de nom ou d'enseigne (BODACC)", 0.8),
    "site_down": ("Site en panne", 0.8),
    "move": ("Déménagement (BODACC)", 0.7),
    "site_diy": ("Site gratuit ou fourni par un annuaire", 0.6),
    "new_establishment": ("Nouvel établissement (BODACC)", 0.6),
    "socials_only": ("Actif sur les réseaux, sans site", 0.5),
    "no_contact_way": ("Site sans formulaire ni e-mail", 0.4),
    "site_builder": ("Site fait avec un créateur de sites (Wix, Jimdo…)", 0.35),
}
ABSENCE_MIN = 0.6         # « pas de site » n'est un fait qu'après une VRAIE recherche (≈ 2/3 des stratégies, moteurs non dégradés)
BONUS_MAX = 12            # points ajoutés au score brut par les signaux d'achat (avant les plafonds)
SITE_OK = ("CONFIRMED", "PROBABLE")
DIY_CODES = ("free_subdomain", "directory_site")
REAL_SIGNAL_MIN = 0.5      # un signal faible (site fait avec Wix sur son propre domaine…) ne suffit pas à garder une activité bruitée


def _months(d, today: date) -> int | None:
    if not d:
        return None
    try:
        d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    except ValueError:
        return None
    return (today.year - d.year) * 12 + today.month - d.month


def _decay(months: int | None) -> float:
    return 0.0 if months is None or months < 0 or months > 12 else 1.0 if months <= 6 else 0.5


def detect(p: dict, today: date | None = None) -> list[dict]:
    """[{'code','label','strength' (0-1),'detail'}] du plus fort au plus faible. `p` : ligne `local_prospects` décodée."""
    today = today or date.today()
    out: dict[str, dict] = {}

    def add(code: str, factor: float, detail: str) -> None:
        strength = round(SIGNALS[code][1] * factor, 2)
        if strength > 0 and strength > out.get(code, {}).get("strength", 0):
            out[code] = {"code": code, "label": SIGNALS[code][0], "strength": strength, "detail": detail}

    bodacc = p.get("bodacc") or {}
    for ev in bodacc.get("events") or []:
        if ev.get("kind") in ("takeover", "rename", "move", "new_establishment"):
            m = _months(ev.get("published"), today)
            add(ev["kind"], _decay(m), f"publié au BODACC le {ev.get('published')} (il y a {m} mois)")
    status = p.get("website_status")
    searched = float(p.get("website_absence_confidence") or 0) >= ABSENCE_MIN      # « aucun site » n'est affirmé qu'après une vraie recherche
    created = _months(p.get("company_created_at"), today)
    if status == "NOT_FOUND" and searched and created is not None and 1 <= created <= 6:
        add("new_no_site", 1.0, f"ouvert il y a {created} mois (SIRENE) et aucun site trouvé")
    elif status == "NOT_FOUND" and searched and created is not None and created == 0:
        add("new_no_site", 0.6, "ouvert ce mois-ci (SIRENE) et aucun site trouvé")
    if status == "UNREACHABLE":
        why = {"SITE_DNS_ERROR": "le domaine ne répond plus (expiré ?)", "SITE_HTTP_ERROR": "le serveur renvoie une erreur",
               "SITE_TIMEOUT": "le serveur ne répond pas"}.get(p.get("error_category") or "", "le site ne répond pas")
        add("site_down", 1.0, why)
    codes = {i.get("code") for i in (p.get("issues") or []) if isinstance(i, dict)}
    if status in SITE_OK and "site_builder" in codes and not codes & set(DIY_CODES):
        add("site_builder", 1.0, next(i.get("evidence", "") for i in p["issues"] if isinstance(i, dict) and i.get("code") == "site_builder") or "créateur de sites")
    if status in SITE_OK and codes & set(DIY_CODES):
        add("site_diy", 1.0, next(i.get("label", "") for i in p["issues"] if isinstance(i, dict) and i.get("code") in DIY_CODES))
    if status == "NOT_FOUND" and searched and p.get("socials"):
        add("socials_only", 1.0, "présent sur les réseaux sociaux mais aucun site trouvé")
    if status in SITE_OK and (p.get("audited_at") is not None or p.get("modernization_opportunity") is not None) \
            and not p.get("contact_form") and not p.get("email"):
        add("no_contact_way", 1.0, "aucun formulaire de contact ni adresse e-mail trouvés sur le site")
    return sorted(out.values(), key=lambda s: -s["strength"])


def bonus(signals: list[dict]) -> float:
    """0-1 : le signal le plus fort, plus un quart de chacun des autres (plusieurs raisons d'acheter maintenant = mieux), plafonné à 1."""
    if not signals:
        return 0.0
    s = sorted((x["strength"] for x in signals), reverse=True)
    return min(1.0, s[0] + 0.25 * sum(s[1:]))


def real_signal(p: dict, sigs: list[dict], today: date | None = None) -> str | None:
    """Un VRAI signal d'intérêt (exigé pour les activités bruitées : restaurants, bars, boulangeries, commerces…), ou None."""
    today = today or date.today()
    status = p.get("website_status")
    strong = [x for x in sigs if x["code"] != "no_contact_way" and x["strength"] >= REAL_SIGNAL_MIN]
    if strong:
        return strong[0]["label"].lower()
    if status == "NOT_FOUND" and float(p.get("website_absence_confidence") or 0) >= ABSENCE_MIN:
        return "aucun site trouvé après une vraie recherche"
    if status == "UNREACHABLE":
        return "site en panne"
    if status in SITE_OK and p.get("modernization_opportunity") in ("HIGH", "MEDIUM"):
        return "site à moderniser (indices objectifs)"
    m = _months(p.get("company_created_at"), today)
    if m is not None and 0 <= m <= 12:
        return f"ouvert il y a {m} mois"
    return None
