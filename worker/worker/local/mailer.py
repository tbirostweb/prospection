"""Composition des e-mails de prospection envoyés automatiquement (aucun réseau ici : tout est testable sans serveur).

Le premier message est le brouillon enregistré tel quel (texte écrit par le code ou par /rediger, avec salutation, signature et ligne
d'opposition). Ce module en fait un e-mail texte + HTML : même texte, signature avec un petit logo dans la version HTML.
Les relances J+3 et J+7 sont courtes, écrites ici, et partent en RÉPONSE au premier message (même fil chez le destinataire).
"""
from __future__ import annotations

import datetime as dt
import html
import re
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, make_msgid
from zoneinfo import ZoneInfo

from .contacts import FREEMAIL

PARIS = ZoneInfo("Europe/Paris")
# personal_emails : envoyer aussi aux adresses Gmail, Orange… publiées comme contact de l'entreprise (choix de Théo, désactivable dans Réglages).
# Envoi étalé pour ne pas faire classer la boîte en spam : 10 h-16 h, pause déjeuner 12 h-13 h, au plus un e-mail par `min_gap_min` minutes
# (premiers messages et relances confondus).
DEFAULTS = {"enabled": False, "daily_cap": 10, "start_hour": 10, "end_hour": 16, "pause_start": 12, "pause_end": 13, "min_gap_min": 60,
            "personal_emails": True, "warmup": True, "bounce_ack_at": None}
WARMUP_STEPS = ((7, 3), (14, 4))   # montée en charge : (jusqu'au jour N exclu, plafond) — jours 0-6 : 3 par jour, jours 7-13 : 4, ensuite le plafond réglé
BOUNCE_WINDOW, BOUNCE_MAX = 10, 2  # coupe-circuit : au moins 2 retours parmi les 10 derniers envois (soit 20 %)
GAP_JITTER_MAX = 10             # minutes ajoutées au-delà de l'intervalle minimum (0 à 10), pour que les envois ne tombent pas toujours à la même minute
FOLLOWUP_DAYS = (3, 7)          # relances : J+3 puis J+7 (comme le suivi manuel)
NO_REPLY_DAYS = 14              # sans réponse à J+14 : abandon, plus aucun contact
AUTO_EMAIL_KINDS = ("GENERIC_BUSINESS", "PERSONAL_BUSINESS")
CLOSING = "Bien cordialement,"
NOTICE_SEP = "\n--\n"
PLACEHOLDERS = ("[Votre prénom et nom]", "[LIEN VERS VOTRE NOTICE")
INK, ACCENT, MUTED = "#231F20", "#F0451E", "#8A8580"
LOGO_BG = "#EFEBE4"                   # fond du logo en texte (même carré beige que le logo image)

DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")

FOLLOWUP_TEXT = {
    "followup1": "Je vous ai écrit {quand} au sujet d'un site internet. Est-ce que c'est un sujet pour vous en ce moment ?",
    "followup2": "Je vous ai écrit la semaine dernière au sujet d'un site internet. Si ça peut vous donner une idée, vous trouverez quelques-unes "
                 "de mes réalisations sur mon site. Je ne vous relancerai plus : si le sujet revient un jour, il suffit de répondre à ce message.",
}


def settings(raw: dict | None) -> dict:
    """Réglage `local_auto_send` normalisé ; absent ou illisible = envoi désactivé."""
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out["enabled"] = raw.get("enabled") is True
        out["personal_emails"] = raw.get("personal_emails") is not False
        out["warmup"] = raw.get("warmup") is not False
        out["bounce_ack_at"] = _naive_utc(raw.get("bounce_ack_at"))   # posé par la route des réglages à chaque enregistrement
        for k, lo, hi in (("daily_cap", 1, 30), ("start_hour", 6, 21), ("end_hour", 7, 22), ("pause_start", 6, 21), ("pause_end", 7, 22),
                       ("min_gap_min", 15, 240)):
            try:
                out[k] = min(hi, max(lo, int(raw.get(k, out[k]))))
            except (TypeError, ValueError):
                pass
    if out["end_hour"] <= out["start_hour"]:
        out["start_hour"], out["end_hour"] = DEFAULTS["start_hour"], DEFAULTS["end_hour"]
    return out


def _naive_utc(v) -> dt.datetime | None:
    """Horodatage ISO (avec ou sans fuseau) en UTC naïf, comme la base ; illisible = None."""
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def effective_cap(cfg: dict, first_sent: dt.datetime | None, now: dt.datetime) -> tuple[int, dt.datetime | None]:
    """Plafond du jour : min(daily_cap, palier d'ancienneté). `first_sent` = plus ancien envoi (UTC naïf) ; aucun envoi = jour 0.
    Retourne aussi la fin du palier en cours (None si plus de palier ou montée en charge désactivée)."""
    if not cfg.get("warmup", True):
        return cfg["daily_cap"], None
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    days = max(0, (t - first_sent).days) if first_sent else 0
    for limit, cap in WARMUP_STEPS:
        if days < limit:
            return min(cfg["daily_cap"], cap), (first_sent or t) + dt.timedelta(days=limit)
    return cfg["daily_cap"], None


def bounce_paused(statuses: list[str]) -> bool:
    """Coupe-circuit : `statuses` = statuts des derniers envois (les plus récents d'abord, après la dernière reprise)."""
    recent = statuses[:BOUNCE_WINDOW]
    return sum(1 for x in recent if x == "bounced") >= BOUNCE_MAX


def _pause(cfg: dict) -> tuple[int, int] | None:
    """Pause (début, fin) si elle est cohérente : non vide et à l'intérieur de la plage d'envoi ; sinon ignorée."""
    a, b = cfg.get("pause_start"), cfg.get("pause_end")
    if isinstance(a, int) and isinstance(b, int) and a < b and cfg["start_hour"] <= a and b <= cfg["end_hour"]:
        return a, b
    return None


def _easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def holiday(day: dt.date) -> bool:
    """Jour férié en France métropolitaine : on n'écrit pas aux entreprises ce jour-là."""
    fixed = {(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)}
    e = _easter(day.year)
    return (day.month, day.day) in fixed or day in {e + dt.timedelta(days=1), e + dt.timedelta(days=39), e + dt.timedelta(days=50)}


def in_window(now: dt.datetime, cfg: dict) -> bool:
    """Jours ouvrés, hors fériés, entre start_hour et end_hour (heure de Paris), hors pause déjeuner (pause_start à pause_end)."""
    local = now.astimezone(PARIS)
    if local.weekday() >= 5 or holiday(local.date()) or not cfg["start_hour"] <= local.hour < cfg["end_hour"]:
        return False
    pause = _pause(cfg)
    return not (pause and pause[0] <= local.hour < pause[1])


def gap_wait(last_sent: dt.datetime | None, last_id: int | None, now: dt.datetime, cfg: dict) -> bool:
    """Vrai si le dernier e-mail envoyé (`last_sent`, UTC naïf comme la base) est trop récent : min_gap_min minutes plus un décalage de 0 à 10 min
    dérivé de l'id de ce dernier e-mail (stable d'un passage à l'autre, différent d'un envoi à l'autre)."""
    if last_sent is None:
        return False
    jitter = ((last_id or 0) * 7919) % (GAP_JITTER_MAX + 1)
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t - last_sent < dt.timedelta(minutes=cfg["min_gap_min"] + jitter)


def paris_midnight_utc(now: dt.datetime) -> dt.datetime:
    """Minuit (heure de Paris) du jour courant, en UTC naïf comme la base : début du plafond quotidien."""
    local = now.astimezone(PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(dt.timezone.utc).replace(tzinfo=None)


def email_domain(addr: str | None) -> str:
    return (addr or "").rpartition("@")[2].strip().lower()


def ineligible(p: dict, personal: bool = False) -> str | None:
    """Raison pour laquelle ce prospect ne reçoit PAS d'e-mail automatique (None = éligible). Revérifié juste avant chaque envoi.
    `personal` : adresses Gmail, Orange… et adresses incertaines acceptées (réglage personal_emails)."""
    email = (p.get("email") or "").strip()
    if p.get("do_not_contact"):
        return "ne plus contacter"
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email, re.I):
        return "pas d'adresse e-mail"
    if personal:
        if p.get("email_kind") not in (*AUTO_EMAIL_KINDS, "UNCERTAIN"):
            return "adresse non vérifiée"
    elif p.get("email_kind") not in AUTO_EMAIL_KINDS or email_domain(email) in FREEMAIL:
        return "adresse non professionnelle ou incertaine"
    body = p.get("draft_message") or ""
    if not body.strip() or not (p.get("draft_subject") or "").strip():
        return "pas de brouillon"
    if any(x in body for x in PLACEHOLDERS):
        return "brouillon incomplet (nom ou notice manquant dans Réglages)"
    if NOTICE_SEP not in body:
        return "ligne d'opposition absente du brouillon"
    return None


def split_draft(body: str) -> tuple[str, str | None, str, str]:
    """(contenu, formule de fin ou None, signature texte, ligne d'information) d'un brouillon construit par lib/draft.ts (wrapDraft)."""
    i = body.rfind(NOTICE_SEP)
    main, notice = (body[:i], body[i + len(NOTICE_SEP):].strip()) if i >= 0 else (body, "")
    j = main.rfind(CLOSING)
    if j < 0:
        return main.strip(), None, "", notice
    return main[:j].strip(), CLOSING, main[j + len(CLOSING):].strip(), notice


def greeting(p: dict) -> str:
    """Même règle que lib/draft.ts (salutation) : jamais de nom ; Monsieur/Madame seulement si la civilité est connue (jamais devinée du prénom)."""
    c = str(p.get("manager_civility") or "").strip().lower().replace(".", "")
    return "Bonjour Monsieur," if c in ("m", "monsieur") else "Bonjour Madame," if c in ("mme", "madame") else "Bonjour,"


def _when(sent_at: dt.datetime, now: dt.datetime) -> str:
    days = (now.date() - sent_at.date()).days
    return DAYS[sent_at.astimezone(PARIS).weekday()] if days <= 6 else "la semaine dernière"


def _as_utc(d: dt.datetime) -> dt.datetime:
    return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d


def quote_header(sent_at: dt.datetime, sender_name: str) -> str:
    local = _as_utc(sent_at).astimezone(PARIS)
    return f"Le {DAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]} {local.year} à {local:%H:%M}, {sender_name} a écrit :"


def followup_body(kind: str, p: dict, first: dict, now: dt.datetime, sender_name: str) -> str:
    """Texte d'une relance : salutation, une ou deux phrases, la même signature et la même ligne d'information, puis le message cité."""
    sent_at = _as_utc(first["sent_at"])
    _content, _closing, signature, notice = split_draft(first["body"])
    line = FOLLOWUP_TEXT[kind].format(quand=_when(sent_at, _as_utc(now)))
    main = first["body"][:first["body"].rfind(NOTICE_SEP)] if NOTICE_SEP in first["body"] else first["body"]
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in main.strip().splitlines())
    return (f"{greeting(p)}\n\n{line}\n\n{CLOSING}\n{signature}{NOTICE_SEP}{notice}\n\n"
            f"{quote_header(sent_at, sender_name)}\n{quoted}")


def _tel(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"+33{digits[1:]}" if len(digits) == 10 and digits.startswith("0") else ("+" + digits if digits else "")


def _linkify(escaped: str) -> str:
    return re.sub(r"(https?://[^\s<>&]+[^\s<>&.,;:!?)])", rf'<a href="\1" style="color:{ACCENT}">\1</a>', escaped)


def _paragraphs(text: str) -> str:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        if block.strip():
            out.append(f'<p style="margin:0 0 14px">{_linkify(html.escape(block.strip())).replace(chr(10), "<br>")}</p>')
    return "".join(out)


def signature_html(sender: dict) -> str:
    """Signature : petit logo à gauche (en texte si `logo_text`, sinon image `logo_url`), nom en gras, fonction, téléphone cliquable et site à droite. Sans bannière."""
    e = html.escape
    name = e(sender.get("name") or "")
    role = " · ".join(e(x) for x in (sender.get("role"), sender.get("company")) if x)
    lines = [f'<strong style="font-size:14px;color:{INK}">{name}</strong>']
    if role:
        lines.append(f'<span style="color:{MUTED}">{role}</span>')
    if sender.get("phone"):
        lines.append(f'<a href="tel:{e(_tel(sender["phone"]))}" style="color:{INK};text-decoration:none">{e(sender["phone"])}</a>')
    site = (sender.get("website") or "").strip()
    if site:
        href = site if site.startswith("http") else f"https://{site}"
        label = re.sub(r"^https?://(www\.)?", "", site).rstrip("/")
        lines.append(f'<a href="{e(href)}" style="color:{ACCENT};text-decoration:none">{e(label)}</a>')
    logo = (sender.get("logo_url") or "").strip()
    mark = (sender.get("logo_text") or "").strip()
    # Logo en texte (prioritaire) : même carré, dessiné en HTML. Sans image, le message évite la pénalité anti-spam HTML_IMAGE_ONLY
    # (mail-tester : 8,9 → 10/10) et le logo s'affiche même quand la messagerie bloque les images. Un point final prend la couleur d'accent.
    if mark:
        dot = mark.endswith(".")
        label = e(mark[:-1] if dot else mark) + (f'<span style="color:{ACCENT}">.</span>' if dot else "")
        logo_cell = (f'<td style="padding:0 12px 0 0;vertical-align:middle"><table cellpadding="0" cellspacing="0" border="0" role="presentation"><tr>'
                     f'<td width="48" height="48" align="center" valign="middle" bgcolor="{LOGO_BG}" style="width:48px;height:48px;background:{LOGO_BG};'
                     f'border-radius:8px;font-family:Arial,Helvetica,sans-serif;font-size:17px;font-weight:bold;line-height:48px;color:{INK}">{label}</td>'
                     f'</tr></table></td>')
    else:
        logo_cell = (f'<td style="padding:0 12px 0 0;vertical-align:middle"><img src="{e(logo)}" width="48" height="48" alt="{e(sender.get("company") or name)}" '
                     f'style="display:block;width:48px;height:48px;border:0;border-radius:8px"></td>') if logo.startswith("https://") and not re.search(r"\.(svg|ico|webp|avif)(\?|#|$)", logo, re.I) else ""
    return (f'<table cellpadding="0" cellspacing="0" border="0" role="presentation" style="margin:4px 0 0;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;line-height:1.45;color:{INK}"><tr>{logo_cell}<td style="border-left:2px solid {ACCENT};padding:0 0 0 12px;'
            f'vertical-align:middle">{"<br>".join(lines)}</td></tr></table>')


def html_body(text: str, sender: dict) -> str:
    """Version HTML du texte : mêmes phrases, signature avec logo, ligne d'information en petit gris, message cité en retrait."""
    quote = ""
    m = re.search(r"\n\n(Le [^\n]+ a écrit :)\n((?:>.*(?:\n|$))+)\s*$", text)
    if m:
        cited = "\n".join(ln[2:] if ln.startswith("> ") else ln[1:] for ln in m.group(2).rstrip("\n").splitlines())
        quote = (f'<p style="margin:18px 0 6px;color:{MUTED};font-size:12px">{html.escape(m.group(1))}</p>'
                 f'<blockquote style="margin:0;padding:0 0 0 10px;border-left:2px solid #D9D5C9;color:#4a4540">{_paragraphs(cited)}</blockquote>')
        text = text[:m.start()]
    content, closing, _sig, notice = split_draft(text)
    parts = [_paragraphs(content)]
    if closing:
        parts.append(f'<p style="margin:0 0 4px">{html.escape(closing)}</p>{signature_html(sender)}')
    if notice:
        parts.append(f'<p style="margin:18px 0 0;font-size:11px;line-height:1.4;color:{MUTED}">{_linkify(html.escape(notice))}</p>')
    return (f'<!doctype html><html><body style="margin:0;padding:0"><div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            f'line-height:1.55;color:{INK};max-width:640px">{"".join(parts)}{quote}</div></body></html>')


def build_message(*, from_addr: str, from_name: str, to: str, subject: str, text: str, sender: dict,
                  in_reply_to: str | None = None, msg_id: str | None = None) -> EmailMessage:
    """E-mail prêt à partir : texte + HTML, Message-ID (pour reconnaître la réponse), désinscription en un clic (mailto)."""
    msg = EmailMessage(policy=SMTP)
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = msg_id or make_msgid(domain=email_domain(from_addr) or None)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg["List-Unsubscribe"] = f"<mailto:{from_addr}?subject=D%C3%A9sinscription>"
    msg.set_content(text)
    msg.add_alternative(html_body(text, sender), subtype="html")
    return msg


def reply_subject(subject: str) -> str:
    return subject if re.match(r"^\s*re\s*:", subject, re.I) else f"Re: {subject}"
