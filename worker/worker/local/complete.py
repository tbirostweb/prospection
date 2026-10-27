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
     la vérification d'avant l'envoi (`presend_site.doubt`) ; ou UNREACHABLE constaté encore injoignable après UNREACHABLE_MAX_TRIES nouvelles
     tentatives espacées de plusieurs jours (`unreachable_retry`, siterecheck.unreachable_retry) : site en panne, c'est tranché ;
     ce qui reste à trancher a une raison (`site_reason`, SITE_REASONS) et chaque raison un passage automatique (`emailsearch`) ;
  5. contact fiable : e-mail GENERIC_BUSINESS / PERSONAL_BUSINESS (UNCERTAIN et messageries gratuites seulement si le réglage personal_emails
     les accepte, comme `mailer.ineligible`), jamais une adresse revenue ou sans serveur de messagerie (auto_state 'bounced') ; à défaut un
     téléphone (fiche complète « téléphone » : pas d'envoi automatique). Sans contact fiable : raison dans `contact_reason` (CONTACT_REASONS).
"""
from __future__ import annotations

import json
import re

from .contacts import FREEMAIL
from .signals import ABSENCE_MIN

AUTO_KINDS = ("GENERIC_BUSINESS", "PERSONAL_BUSINESS")
CLOSED_BODACC = ("radiation", "collective", "closure")
UNKNOWN_ACTIVITY = ("", "non classée")
UNREACHABLE_MAX_TRIES = 2              # nouvelles tentatives d'un site injoignable (siterecheck.unreachable_retry) avant de le dire « en panne »

# Ce qui manque, dans l'ordre d'affichage (libellés repris tels quels par web/lib/complete.ts).
LABELS = {
    "entreprise": "entreprise écartée, chaîne, fermée ou « ne plus contacter »",
    "traitement": "traitement pas terminé",
    "metier": "métier inconnu (activité non classée)",
    "site": "site à trancher",
    "email_invalide": "e-mail invalide",
    "contact": "aucun contact fiable",
}

# Pourquoi le site est encore « à trancher » (site_reason), dans l'ordre d'affichage ; chaque raison a son passage automatique (emailsearch.main).
SITE_REASONS = {
    "review": "site importé à vérifier par toi",
    "search": "recherche moteur pas faite ou trop maigre",
    "recheck": "revérification pas encore faite",
    "places": "site de la fiche Google pas vérifié",
    "presend": "doute à la vérification d'avant l'envoi",
    "uncertain": "site incertain, revérification à faire",
    "uncertain_user": "site incertain après revérification : à trancher par toi",
    "unreachable": "site injoignable, nouvel essai prévu",
}
# Pourquoi il n'y a pas de contact fiable (contact_reason) : ni e-mail acceptable ni téléphone.
CONTACT_REASONS = {
    "invalid": "e-mail invalide (revenu ou sans serveur de messagerie)",
    "refused": "e-mail refusé (incertain ou messagerie gratuite, voir Réglages)",
    "form": "seulement un formulaire ou une page de contact",
    "none": "ni e-mail ni téléphone",
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


def unreachable_settled(p: dict) -> bool:
    """Site injoignable revu UNREACHABLE_MAX_TRIES fois, à plusieurs jours d'écart, et toujours injoignable : « site en panne », c'est tranché."""
    r = _json(p.get("provenance")).get("unreachable_retry")
    return isinstance(r, dict) and int(r.get("tries") or 0) >= UNREACHABLE_MAX_TRIES and r.get("status") == "UNREACHABLE"


def site_reason(p: dict) -> str | None:
    """Pourquoi le site est encore à trancher (code de SITE_REASONS), ou None s'il est tranché."""
    status = p.get("website_status")
    if status in ("CONFIRMED", "PROBABLE"):
        return "review" if p.get("site_review") == "PENDING" else None
    prov = _json(p.get("provenance"))
    recheck = prov.get("site_recheck") if isinstance(prov.get("site_recheck"), dict) else {}
    if status == "UNCERTAIN":
        return "uncertain" if recheck.get("status") is None else "uncertain_user"
    if status == "UNREACHABLE":
        return None if unreachable_settled(p) else "unreachable"
    if status != "NOT_FOUND" or float(p.get("website_absence_confidence") or 0) < ABSENCE_MIN:
        return "search"                                    # pas encore cherché, ou recherche trop courte / dégradée
    if recheck.get("status") is None:
        return "recheck"                                   # revérification jamais faite (ou en erreur)
    if isinstance(prov.get("presend_site"), dict) and prov["presend_site"].get("doubt"):
        return "presend"
    places = prov.get("places") if isinstance(prov.get("places"), dict) else {}
    if places.get("had_website") is True and places.get("site_checked_at") is None:
        return "places"
    return None


def site_state(p: dict) -> str:
    """« ok » (site tranché), « places » (tranché, sauf le site de la fiche Google encore à vérifier) ou « todo »."""
    why = site_reason(p)
    return "ok" if why is None else "places" if why == "places" else "todo"


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


def contact_reason(p: dict, personal: bool) -> str | None:
    """Pourquoi la fiche n'a pas de contact fiable (code de CONTACT_REASONS), ou None (e-mail acceptable ou téléphone)."""
    if email_ok(p, personal) or phone_ok(p):
        return None
    if email_invalid(p):
        return "invalid"
    if (p.get("email") or "").strip():
        return "refused"
    if int(p.get("contact_form") or 0) or (p.get("contact_page") or "").strip():
        return "form"
    return "none"


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
