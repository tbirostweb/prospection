"""« Fiche complète » : LE critère unique de qualité d'une fiche (miroir exact de web/lib/complete.ts, mêmes cas de test :
worker/tests/fixtures/fiche_complete_cases.json). Qualité plutôt que quantité : seule une fiche complète AVEC e-mail part par l'envoi automatique
(vérifié juste avant l'envoi, `outbox`) ; une fiche complète « téléphone » se contacte à la main ; les autres attendent que l'app les complète.

Une fiche est complète quand, à la fois :
  1. entreprise active et indépendante : ni « ne plus contacter », ni écartée (excluded_reason), ni chaîne, ni « Ignorer », pas de radiation /
     procédure collective / fermeture connue (BODACC) ;
  2. traitement terminé (pipeline_stage = DONE) ;
  3. métier connu : activité issue du code NAF (activity_label), jamais devinée d'après le nom ;
  4. site tranché : CONFIRMED / PROBABLE (hors site importé « à vérifier par toi »), ou NOT_FOUND après la recherche complète — recherche
     moteur faite (confiance d'absence >= ABSENCE_MIN), revérification `site_recheck` faite (devinettes de domaine et domaine de l'e-mail
     compris), site de la fiche Google vérifié s'il y en avait un (`places.had_website` → `places.site_checked_at`), aucun doute laissé par
     la vérification d'avant l'envoi (`presend_site.doubt`) ;
  5. contact fiable : e-mail GENERIC_BUSINESS / PERSONAL_BUSINESS (UNCERTAIN et messageries gratuites seulement si le réglage personal_emails
     les accepte, comme `mailer.ineligible`), jamais une adresse revenue ou sans serveur de messagerie (auto_state 'bounced') ; à défaut un
     téléphone (fiche complète « téléphone » : pas d'envoi automatique).
"""
from __future__ import annotations

import json
import re

from .contacts import FREEMAIL
from .signals import ABSENCE_MIN

AUTO_KINDS = ("GENERIC_BUSINESS", "PERSONAL_BUSINESS")
CLOSED_BODACC = ("radiation", "collective", "closure")
UNKNOWN_ACTIVITY = ("", "non classée")

# Ce qui manque, dans l'ordre d'affichage (libellés repris tels quels par web/lib/complete.ts).
LABELS = {
    "entreprise": "entreprise écartée, chaîne, fermée ou « ne plus contacter »",
    "traitement": "traitement pas terminé",
    "metier": "métier inconnu (activité non classée)",
    "site": "site à trancher",
    "email_invalide": "e-mail invalide",
    "contact": "aucun contact fiable",
}


def _json(v) -> dict:
    if isinstance(v, (str, bytes)):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def active(p: dict) -> bool:
    return (not int(p.get("do_not_contact") or 0) and p.get("status") != "DO_NOT_CONTACT" and not p.get("excluded_reason")
            and not int(p.get("is_chain") or 0) and (p.get("category") or "") != "IGNORER"
            and (_json(p.get("bodacc")).get("kind") or "") not in CLOSED_BODACC)


def activity_known(p: dict) -> bool:
    return (p.get("activity_label") or "").strip().lower() not in UNKNOWN_ACTIVITY


def site_state(p: dict) -> str:
    """« ok » (site tranché), « places » (tranché, sauf le site de la fiche Google encore à vérifier) ou « todo »."""
    status = p.get("website_status")
    if status in ("CONFIRMED", "PROBABLE"):
        return "todo" if p.get("site_review") == "PENDING" else "ok"
    if status != "NOT_FOUND" or float(p.get("website_absence_confidence") or 0) < ABSENCE_MIN:
        return "todo"
    prov = _json(p.get("provenance"))
    if not isinstance(prov.get("site_recheck"), dict) or prov["site_recheck"].get("status") is None:
        return "todo"                                      # revérification jamais faite (ou en erreur)
    if isinstance(prov.get("presend_site"), dict) and prov["presend_site"].get("doubt"):
        return "todo"
    places = prov.get("places") if isinstance(prov.get("places"), dict) else {}
    if places.get("had_website") is True and places.get("site_checked_at") is None:
        return "places"
    return "ok"


def email_invalid(p: dict) -> bool:
    return bool((p.get("email") or "").strip()) and p.get("auto_state") == "bounced"


def email_ok(p: dict, personal: bool) -> bool:
    email = (p.get("email") or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email, re.I) or email_invalid(p):
        return False
    if personal:
        return p.get("email_kind") in (*AUTO_KINDS, "UNCERTAIN")
    return p.get("email_kind") in AUTO_KINDS and email.rpartition("@")[2] not in FREEMAIL


def phone_ok(p: dict) -> bool:
    return bool((p.get("phone") or "").strip())


def missing(p: dict, personal: bool) -> list[str]:
    """Codes de ce qui manque (vide = fiche complète), dans l'ordre de LABELS."""
    out = []
    if not active(p):
        out.append("entreprise")
    if p.get("pipeline_stage") != "DONE":
        out.append("traitement")
    if not activity_known(p):
        out.append("metier")
    if site_state(p) != "ok":
        out.append("site")
    if not email_ok(p, personal) and not phone_ok(p):
        out.append("email_invalide" if email_invalid(p) else "contact")
    return out


def channel(p: dict, personal: bool) -> str | None:
    """'email' (fiche complète, envoi automatique possible), 'phone' (complète, à contacter soi-même) ou None (incomplète)."""
    if missing(p, personal):
        return None
    return "email" if email_ok(p, personal) else "phone"


def reason(p: dict, personal: bool) -> str | None:
    """Pourquoi l'envoi automatique est refusé par ce critère (None = fiche complète avec e-mail)."""
    codes = missing(p, personal)
    if codes:
        return "fiche incomplète : " + ", ".join(LABELS[c] for c in codes)
    return None if email_ok(p, personal) else "pas d'e-mail fiable : à contacter par téléphone"
