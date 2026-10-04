"""Notifications Telegram de la prospection locale.

Formatage en HTML (et non Markdown) : tout contenu dynamique (nom d'entreprise, URL, message d'erreur…) est échappé. En Markdown, un simple `_`
ou `*` non appairé — fréquent dans les URLs — faisait rejeter le message ENTIER par Telegram.
Une alerte = un prospect « À contacter » ou « Très bon » (jamais un contact automatique) avec des boutons de classement : ⭐ 📞 🚫 ❌ Mauvais site.
"""
from __future__ import annotations

import html
import re

import httpx

from .config import APP_URL, TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, log

# Dernière erreur renvoyée par Telegram (lue par `worker.telegram_check`).
LAST_ERROR: str | None = None
CATEGORY_ICON = {"TRES_BON": "🔥", "A_CONTACTER": "🟢", "A_EXAMINER": "🟡"}
CATEGORY_LABEL = {"TRES_BON": "Très bon prospect", "A_CONTACTER": "À contacter", "A_EXAMINER": "À examiner"}
SITE_LABEL = {"CONFIRMED": "site confirmé", "PROBABLE": "site probable", "UNCERTAIN": "site incertain", "NOT_FOUND": "site non trouvé", "UNREACHABLE": "site inaccessible"}
DISLIKE_REASONS = [("not_relevant", "Pas pertinent"), ("site_ok", "Site finalement bon"), ("wrong_site", "Mauvais site associé"), ("chain", "Chaîne"),
                   ("agency_client", "Déjà client d'une agence"), ("no_need", "Aucun besoin évident"), ("no_contact", "Contact impossible"), ("other", "Autre")]


def esc(value) -> str:
    """Échappe un contenu dynamique pour le mode HTML de Telegram."""
    return html.escape("" if value is None else str(value), quote=False)


def _post(payload: dict) -> tuple[bool, str | None]:
    """Appelle sendMessage. Renvoie (ok, description de l'erreur Telegram)."""
    try:
        resp = httpx.post(
            f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload, timeout=15,
        )
    except httpx.HTTPError as exc:
        return False, f"réseau : {exc}"
    if resp.status_code == 200:
        return True, None
    # Telegram explique précisément le refus : on le garde, c'est LE diagnostic.
    try:
        desc = resp.json().get("description")
    except ValueError:
        desc = resp.text[:200]
    return False, f"{resp.status_code} {desc}"


def send_text(text: str, disable_preview: bool = True, reply_markup: dict | None = None) -> bool:
    """Envoie un message (HTML) sur le canal Telegram configuré."""
    global LAST_ERROR
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LAST_ERROR = "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID non défini"
        log.warning("[notify] Telegram non configuré (%s) : message ignoré", LAST_ERROR)
        return False

    payload: dict = {
        "chat_id": TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": disable_preview,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    ok, err = _post(payload)
    if not ok and err and "parse" in err.lower():
        # Formatage refusé malgré l'échappement : on renvoie en texte brut
        # plutôt que de perdre le message.
        log.warning("[notify] formatage refusé (%s) : renvoi en texte brut", err)
        payload.pop("parse_mode", None)
        payload["text"] = html.unescape(re.sub(r"<[^>]+>", "", text))
        ok, err = _post(payload)

    LAST_ERROR = err
    if not ok:
        log.warning("[notify] échec Telegram : %s", err)
    return ok


def format_prospect(p: dict, issues: list[dict] | None = None, summary: list[str] | None = None) -> str:
    """Message d'alerte : qui, où, site + confiance, pourquoi, problèmes objectifs, contact. Jamais « cherche un développeur » : c'est un prospect à froid."""
    name = (p.get("trade_name") or p.get("company_name") or "").strip().title()
    cat = p.get("category") or "A_CONTACTER"
    lines = [f"{CATEGORY_ICON.get(cat, '•')} <b>{esc(name)}</b> — {int(p.get('prospect_score') or 0)}/100 · {CATEGORY_LABEL.get(cat, cat)}"]
    where = [x for x in (p.get("activity_label"), p.get("city"), f"{float(p['distance_km']):.1f} km".replace(".", ",") if p.get("distance_km") is not None else None) if x]
    lines.append(esc(" · ".join(where)))
    site = SITE_LABEL.get(p.get("website_status") or "", "site pas encore recherché")
    if p.get("website_confidence") is not None and p.get("website_status") in ("CONFIRMED", "PROBABLE"):
        site += f" ({round(float(p['website_confidence']) * 100)} %)"
    if p.get("website_status") == "NOT_FOUND":
        site += " — ne prouve pas l'absence de site"
    lines.append(f"🌐 {esc(site)}")
    for s in (summary or [])[:2]:
        lines.append(f"• {esc(s)}")
    bad = [i["label"] for i in (issues or []) if i.get("severity") in ("high", "medium")][:2]
    if bad:
        lines.append("⚠ " + esc(" · ".join(bad)))
    contact = [x for x in ("email disponible sur la fiche" if p.get("email") else None, "téléphone disponible sur la fiche" if p.get("phone") else None, "formulaire de contact" if p.get("contact_form") else None) if x]
    lines.append("📇 " + (esc(" · ".join(contact)) if contact else "aucune coordonnée trouvée"))
    if p.get("data_confidence_score") is not None:
        lines.append(f"Fiabilité des données : {int(p['data_confidence_score'])}/100")
    lines.append("<i>Prospect à froid : aucune demande publiée.</i>")
    if APP_URL:
        lines.append(f"🔗 {esc(APP_URL)}/local/{int(p['id'])}")
    return "\n".join(lines)


def build_keyboard(pid: int, has_site: bool = False) -> dict:
    """Classement depuis le téléphone. `callback_data` ≤ 64 octets : `p:<id>:<action>` ; traité par `worker.telegram_poll`."""
    rows = [[{"text": "⭐ Bon prospect", "callback_data": f"p:{pid}:star"}, {"text": "🚫 Pas intéressé", "callback_data": f"p:{pid}:no"}],
            [{"text": "📞 Contacté", "callback_data": f"p:{pid}:called"}]]
    if has_site:
        rows[1].append({"text": "❌ Mauvais site", "callback_data": f"p:{pid}:wrong"})
    return {"inline_keyboard": rows}


def reasons_keyboard(pid: int) -> dict:
    """2e étape du 🚫 : POURQUOI (sert à comprendre les erreurs du score, jamais à entraîner un modèle)."""
    rows, row = [], []
    for key, label in DISLIKE_REASONS:
        row.append({"text": label, "callback_data": f"p:{pid}:r:{key}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    return {"inline_keyboard": rows + ([row] if row else [])}


def send_prospect(p: dict, issues: list[dict] | None = None, summary: list[str] | None = None) -> bool:
    has_site = p.get("website_status") in ("CONFIRMED", "PROBABLE", "UNCERTAIN", "UNREACHABLE") and bool(p.get("website_url"))
    return send_text(format_prospect(p, issues, summary), disable_preview=True, reply_markup=build_keyboard(int(p["id"]), has_site))
