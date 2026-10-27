"""Vérification DNS du domaine d'envoi avant tout e-mail automatique (DNS sur HTTPS : aucune dépendance en plus).

Bloquant : SPF absent ou en double, DMARC absent ou en double (deux enregistrements DMARC = DMARC ignoré par les messageries), DKIM introuvable
quand un sélecteur est déclaré (LOCAL_DKIM_SELECTOR). Sans sélecteur, DKIM n'est pas vérifiable : simple avertissement.
Avant chaque PREMIER e-mail : le domaine du destinataire doit pouvoir recevoir du courrier (`mail_host` : MX, ou A / AAAA en repli, RFC 5321).
"""
from __future__ import annotations

import httpx

RESOLVERS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")


TYPES = {"A": 1, "MX": 15, "TXT": 16, "AAAA": 28}


def doh(name: str, rtype: str = "TXT", timeout: float = 8.0) -> list[str]:
    """Données des enregistrements `rtype` de `name` (CNAME suivis). Lève RuntimeError si aucun résolveur ne répond."""
    last: Exception | None = None
    for url in RESOLVERS:
        try:
            r = httpx.get(url, params={"name": name, "type": rtype}, headers={"accept": "application/dns-json"}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if data.get("Status") not in (0, 3):                 # 3 = NXDOMAIN : nom absent, réponse valable
                raise RuntimeError(f"réponse DNS {data.get('Status')}")
            return [a["data"] for a in data.get("Answer") or [] if a.get("type") == TYPES[rtype]]
        except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as exc:
            last = exc
    raise RuntimeError(f"DNS injoignable ({last})")


def doh_txt(name: str, timeout: float = 8.0) -> list[str]:
    """Enregistrements TXT de `name` (CNAME suivis). Lève RuntimeError si aucun résolveur ne répond."""
    return ["".join(part.strip('"') for part in d.split('" "')) for d in doh(name, "TXT", timeout)]


def mail_host(domain: str, query=doh) -> bool:
    """Le domaine peut-il recevoir du courrier ? MX (hors « MX nul » RFC 7505 : « 0 . »), sinon A ou AAAA (RFC 5321 §5.1).
    `query(name, type)` remplaçable en test. Lève RuntimeError si le DNS ne répond pas (ne rien conclure)."""
    mx = query(domain, "MX")
    if mx:
        return any(d.split()[-1].strip(".") for d in mx if d.split())   # seulement des « MX nuls » : le domaine refuse tout courrier
    return bool(query(domain, "A") or query(domain, "AAAA"))


def check(domain: str, selector: str | None = None, txt=doh_txt) -> dict:
    """{'ok': bool, 'problems': [...], 'warnings': [...]} pour le domaine de l'adresse d'envoi."""
    problems, warnings = [], []
    try:
        spf = [t for t in txt(domain) if t.lower().startswith("v=spf1")]
        dmarc = [t for t in txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
        dkim = [t for t in txt(f"{selector}._domainkey.{domain}") if "p=" in t] if selector else None
    except RuntimeError as exc:
        return {"ok": False, "problems": [f"vérification DNS impossible : {exc}"], "warnings": []}
    if not spf:
        problems.append(f"aucun enregistrement SPF sur {domain}")
    elif len(spf) > 1:
        problems.append(f"{len(spf)} enregistrements SPF sur {domain} : n'en garder qu'un")
    if not dmarc:
        problems.append(f"aucun enregistrement DMARC sur _dmarc.{domain}")
    elif len(dmarc) > 1:
        problems.append(f"{len(dmarc)} enregistrements DMARC sur _dmarc.{domain} : n'en garder qu'un (sinon DMARC est ignoré)")
    elif "p=none" in dmarc[0].replace(" ", "").lower():
        warnings.append("DMARC en p=none : accepté, mais p=quarantine protège mieux ton domaine")
    if selector is None:
        warnings.append("DKIM non vérifié : renseigne LOCAL_DKIM_SELECTOR (le sélecteur affiché par OVH) pour le contrôler")
    elif not dkim:
        problems.append(f"aucune clé DKIM sur {selector}._domainkey.{domain}")
    return {"ok": not problems, "problems": problems, "warnings": warnings}
