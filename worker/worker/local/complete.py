"""« Fiche complète » : LE critère unique de qualité d'une fiche (miroir exact de web/lib/complete.ts, mêmes cas de test :
worker/tests/fixtures/fiche_complete_cases.json). Une fiche complète « téléphone » se contacte à la main ; les autres attendent que l'app les complète.

L'E-MAIL D'ABORD (règle du propriétaire) : le site ne bloque plus l'envoi automatique, il ORIENTE le message (`angle`). Une fiche dont seul le
site reste « à trancher » part quand même par e-mail (`reason` ignore le code « site ») ; « site à trancher » reste affiché et traité par les
passages automatiques (`emailsearch`), et compte toujours dans la qualité de la liste. Angle du message (`angle`, miroir de ficheAngle) :
  * « refonte »  : site vérifié (CONFIRMED / PROBABLE hors site importé à vérifier, ou confirmé par toi) et pas mis en doute juste avant l'envoi ;
  * « creation » : NOT_FOUND tranché (recherche complète, cf. site_reason ; site de la fiche Google vérifié juste avant l'envoi) ;
  * « neutre »   : tout le reste (UNCERTAIN, UNREACHABLE, site candidat non prouvé, NOT_FOUND pas encore tranché) : le message ne parle ni d'un
    site existant ni d'une absence de site.
Un brouillon dont l'angle (`draft_angle`, d'après draft_kind) ne correspond plus à l'état du site n'est jamais envoyé : `outbox` l'efface et la
fiche revient dans la file de rédaction (/rediger), qui le réécrit avec le bon angle.

Une fiche est complète quand, à la fois :
  1. entreprise active et indépendante : ni « ne plus contacter », ni écartée (excluded_reason : posée aussi par companystate.py quand SIRENE /
     BODACC disent l'établissement fermé, l'entreprise cessée, radiée ou en procédure collective), ni chaîne, ni « Ignorer », pas de radiation /
     procédure collective / fermeture connue (BODACC) ;
  2. traitement terminé (pipeline_stage = DONE) ;
  3. métier connu : activité issue du code NAF (activity_label), jamais devinée d'après le nom ;
  4. site tranché : CONFIRMED / PROBABLE (hors site importé « à vérifier par toi »), ou NOT_FOUND après la recherche complète — recherche
     moteur faite (confiance d'absence >= ABSENCE_MIN), revérification `site_recheck` faite (devinettes de domaine et domaine de l'e-mail
     compris), site de la fiche Google vérifié s'il y en avait un (`places.had_website` → `places.site_checked_at`), aucun doute laissé par
     la vérification d'avant l'envoi (`presend_site.doubt` ; resté après les devinettes refaites : « presend_user », à trancher par toi) ; ou UNREACHABLE constaté encore injoignable après UNREACHABLE_MAX_TRIES nouvelles
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
    "presend_user": "doute à la vérification d'avant l'envoi, toujours là après les devinettes : à trancher par toi",
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
    pre = prov.get("presend_site") if isinstance(prov.get("presend_site"), dict) else {}
    if pre.get("doubt"):
        guess = prov.get("site_guess") if isinstance(prov.get("site_guess"), dict) else {}
        # les devinettes refaites APRÈS le doute ne l'ont pas levé (site possible, pas assez sûr) : plus aucun passage automatique, c'est à toi
        done = str(guess.get("checked_at") or "")
        return "presend_user" if done and done >= str(pre.get("at") or "") else "presend"
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
    """Pourquoi l'envoi automatique est refusé par ce critère (None = e-mail utilisable). Le site n'en fait plus partie : il oriente le message
    (`angle`), il ne bloque pas."""
    codes = [c for c in missing(p, personal) if c != "site"]
    if codes:
        return "fiche incomplète : " + ", ".join(LABELS[c] for c in codes)
    return None if email_ok(p, personal) else "pas d'e-mail fiable : à contacter par téléphone"


ANGLES = ("refonte", "creation", "neutre")


def site_verified(p: dict) -> bool:
    """Site de l'entreprise vérifié : CONFIRMED / PROBABLE (hors site importé à vérifier) ou confirmé par toi, et pas mis en doute par la
    relecture d'avant l'envoi (`presend_identity.doubt` sur cette même adresse : site qui ne répondait plus)."""
    url = (p.get("website_url") or "").strip()
    if not url:
        return False
    if p.get("site_review") == "CONFIRMED":
        return True
    if p.get("website_status") not in ("CONFIRMED", "PROBABLE") or p.get("site_review") == "PENDING":
        return False
    pre = _json(p.get("provenance")).get("presend_identity")
    return not (isinstance(pre, dict) and pre.get("doubt") and pre.get("url") == url[:200])


def angle(p: dict) -> str:
    """Angle du message d'après l'état du site : 'refonte', 'creation' ou 'neutre' (voir l'en-tête)."""
    if site_verified(p):
        return "refonte"
    if p.get("website_status") == "NOT_FOUND" and site_reason(p) in (None, "places"):
        return "creation"
    return "neutre"


def draft_angle(kind: str | None) -> str | None:
    """Angle d'un brouillon d'après son type (draft_kind, lib/draft.ts : préfixes « ai_ » et événement compris). None : type inconnu (brouillon
    écrit à la main sans passer par l'application). draft_kind est coupé à 24 caractères (« ai_takeover_site_amelior ») : préfixes seulement."""
    k = kind or ""
    if "neutre" in k:
        return "neutre"
    if "sans_site" in k:
        return "creation"
    if re.search(r"site_(?:amel|refo|inac)", k):
        return "refonte"
    return None
