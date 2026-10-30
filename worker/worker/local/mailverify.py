"""La boîte du destinataire existe-t-elle ? Vérification SMTP AVANT le premier message (jamais avant une relance).

Dialogue avec le serveur de messagerie du destinataire (port 25, MX par ordre de priorité) : EHLO avec le domaine de l'expéditeur, STARTTLS s'il est
proposé, MAIL FROM:<expéditeur> (pas <> : certains serveurs le refusent), RCPT TO:<adresse>, puis RCPT TO d'une adresse aléatoire du même domaine
(si elle passe aussi, le domaine accepte tout : « catch_all », la boîte n'est pas prouvée), enfin RSET et QUIT. JAMAIS de DATA : aucun message ne part.

Résultat :
  * 'valid'     : 250 / 251 sur l'adresse, refus de l'adresse aléatoire ;
  * 'catch_all' : l'adresse aléatoire passe aussi (rien de prouvé, l'envoi continue) ;
  * 'invalid'   : 550 / 551 / 553 avec une réponse CLAIRE de boîte inexistante (5.1.1, « user unknown », « no such user »…) ;
  * 'unknown'   : tout le reste — 4xx (greylisting), délai dépassé, connexion refusée ou port 25 bloqué, 5xx de politique (IP bloquée, « spamhaus »,
    « blocked », « policy », « relay », 5.7.x…), réponse inattendue. L'envoi n'est JAMAIS bloqué sur 'unknown'.
Un seul essai (deux serveurs MX au plus, seulement si le premier ne répond pas). `check` renvoie le détail ; `verify` seulement le résultat."""
from __future__ import annotations

import re
import secrets
import smtplib
import ssl

TIMEOUT = 10.0                      # secondes par connexion / par réponse
MAX_HOSTS = 2                       # serveurs MX essayés au plus (le suivant seulement si le précédent ne répond pas)

# Réponse de politique : le refus ne dit rien de la boîte (IP ou expéditeur bloqués, relais interdit…) -> 'unknown', même en 550.
POLICY = re.compile(r"spamhaus|spamcop|barracuda|sorbs|blocklist|block list|blacklist|blocked|\brbl\b|\bdnsbl\b|policy|denied|reputation|banned|"
                    r"not authori[sz]ed|relay|spam|abuse|\b5\.7\.\d+|\b5\.2\.[12]\b|quota|mailbox full|over quota|greylist|try again|"
                    r"too many|rate limit|client host|ip address|your ip|sender", re.I)
# Réponse claire de boîte inexistante.
NO_MAILBOX = re.compile(r"\b5\.1\.[01]\b|user unknown|unknown user|no such user|no such (?:recipient|mailbox|address)|does not exist|doesn'?t exist|"
                        r"not exist|mailbox (?:not found|unavailable|does not|is unavailable)|recipient (?:not found|unknown|address rejected)|"
                        r"invalid (?:recipient|mailbox|address)|unknown recipient|address rejected|account (?:disabled|does not exist)|"
                        r"utilisateur inconnu|destinataire inconnu|n'existe pas|boîte inconnue|adresse inconnue|inexistant", re.I)


class _Unreachable(Exception):
    """Pas de dialogue possible avec ce serveur (connexion refusée, délai dépassé, coupure) : on essaie le suivant."""


def _text(resp) -> str:
    return (resp.decode("utf-8", "replace") if isinstance(resp, (bytes, bytearray)) else str(resp or "")).strip()


def classify(code: int, msg: str) -> str:
    """Réponse à RCPT TO : 'ok' (acceptée), 'nobox' (boîte clairement inexistante) ou 'other' (4xx, politique, doute)."""
    if code in (250, 251):
        return "ok"
    if code in (550, 551, 553) and not POLICY.search(msg) and NO_MAILBOX.search(msg):
        return "nobox"
    return "other"


def _result(result: str, code: int | None, detail: str, connected: bool = True) -> dict:
    return {"result": result, "code": code, "detail": detail[:160], "connected": connected}


def _dialogue(srv, address: str, mail_from: str, helo: str) -> dict:
    srv.ehlo(helo)
    if srv.has_extn("starttls"):
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE         # chiffrement opportuniste : le certificat d'un MX n'est pas un critère
        srv.starttls(context=ctx)
        srv.ehlo(helo)
    code, resp = srv.mail(mail_from)
    if code != 250:
        return _result("unknown", code, "MAIL FROM refusé : " + _text(resp))
    code, resp = srv.rcpt(address)
    msg = _text(resp)
    kind = classify(code, msg)
    if kind == "nobox":
        out = _result("invalid", code, msg)
    elif kind == "other":
        out = _result("unknown", code, msg)
    else:
        domain = address.rsplit("@", 1)[-1]
        rcode, rresp = srv.rcpt(f"verif-{secrets.token_hex(6)}@{domain}")     # adresse qui n'existe sûrement pas : si elle passe, tout passe
        out = _result("catch_all" if rcode in (250, 251) else "valid", code, msg if rcode not in (250, 251) else f"adresse aléatoire acceptée aussi ({rcode})")
    try:
        srv.rset()
    except (smtplib.SMTPException, OSError):
        pass
    return out


def check(address: str, mx_hosts: list[str], helo: str, mail_from: str, timeout: float = TIMEOUT, factory=smtplib.SMTP, port: int = 25) -> dict:
    """{'result', 'code', 'detail', 'connected'} : `connected` est faux si AUCUN serveur n'a répondu (port 25 fermé, délai dépassé…).
    `factory(host, port, timeout=, local_hostname=)` remplaçable en test. Ne lève jamais."""
    last = "aucun serveur de messagerie"
    for host in mx_hosts[:MAX_HOSTS]:
        srv = None
        try:
            try:
                srv = factory(host, port, timeout=timeout, local_hostname=helo)
            except smtplib.SMTPConnectError as exc:                       # le serveur répond mais refuse d'emblée (bannière 4xx / 554 : IP bloquée…)
                return _result("unknown", exc.smtp_code, f"{host} : bannière refusée {_text(exc.smtp_error)}")
            except (OSError, smtplib.SMTPException) as exc:
                raise _Unreachable(f"{host} : {type(exc).__name__} {exc}") from exc
            try:
                return _dialogue(srv, address, mail_from, helo)
            except (smtplib.SMTPServerDisconnected, OSError, ssl.SSLError) as exc:      # coupure en cours de dialogue (délai compris)
                raise _Unreachable(f"{host} : {type(exc).__name__} {exc}") from exc
            except smtplib.SMTPException as exc:
                return _result("unknown", getattr(exc, "smtp_code", None), f"{host} : {type(exc).__name__}")
        except _Unreachable as exc:
            last = str(exc)
        finally:
            if srv is not None:
                try:
                    srv.quit()
                except Exception:  # noqa: BLE001 — la session est abandonnée de toute façon
                    try:
                        srv.close()
                    except Exception:  # noqa: BLE001
                        pass
    return _result("unknown", None, "connexion impossible : " + last, connected=False)


def verify(address: str, mx_hosts: list[str], helo: str, mail_from: str, timeout: float = TIMEOUT, factory=smtplib.SMTP) -> str:
    """'valid' | 'invalid' | 'catch_all' | 'unknown' (voir `check` pour le code et le détail)."""
    return check(address, mx_hosts, helo, mail_from, timeout, factory)["result"]
