"""Vérification SMTP d'une boîte (mailverify) avec un faux serveur : aucun réseau, aucun DATA."""
from __future__ import annotations

import smtplib

import pytest

from worker.local import mailverify

ADDR = "contact@garage-exemple.fr"


class FakeSrv:
    """Faux smtplib.SMTP : `rcpt_replies` {adresse ou 'random': (code, message)} ; enregistre les commandes reçues."""

    def __init__(self, rcpt, mail=(250, b"ok"), tls=False, fail_on=None):
        self.rcpt_replies, self.mail_reply, self.tls, self.fail_on = rcpt, mail, tls, fail_on
        self.cmds: list[str] = []

    def ehlo(self, name=None):
        self.cmds.append(f"EHLO {name}")
        return 250, b"ok"

    def has_extn(self, name):
        return self.tls and name == "starttls"

    def starttls(self, context=None):
        self.cmds.append("STARTTLS")
        return 220, b"go"

    def mail(self, addr):
        self.cmds.append(f"MAIL {addr}")
        if self.fail_on == "mail":
            raise smtplib.SMTPServerDisconnected("coupé")
        return self.mail_reply

    def rcpt(self, addr):
        self.cmds.append(f"RCPT {addr}")
        if self.fail_on == "rcpt":
            raise TimeoutError("délai")
        return self.rcpt_replies["random" if addr.startswith("verif-") else addr]

    def rset(self):
        self.cmds.append("RSET")
        return 250, b"ok"

    def quit(self):
        self.cmds.append("QUIT")

    def close(self):
        pass

    def data(self, *a, **k):                                                   # jamais appelé
        raise AssertionError("DATA interdit")


def run(servers, hosts=("mx1.exemple.net",)):
    """`servers` : {hôte: FakeSrv ou exception levée à la connexion}."""
    def factory(host, port, timeout=None, local_hostname=None):
        assert port == 25 and timeout == 10.0 and local_hostname == "exemple-web.fr"
        s = servers[host]
        if isinstance(s, Exception):
            raise s
        return s
    return mailverify.check(ADDR, list(hosts), "exemple-web.fr", "contact@exemple-web.fr", 10.0, factory)


def test_boite_valide():
    s = FakeSrv({ADDR: (250, b"2.1.5 ok"), "random": (550, b"5.1.1 User unknown")}, tls=True)
    r = run({"mx1.exemple.net": s})
    assert r["result"] == "valid" and r["code"] == 250 and r["connected"]
    assert s.cmds[:3] == ["EHLO exemple-web.fr", "STARTTLS", "EHLO exemple-web.fr"]
    assert "MAIL contact@exemple-web.fr" in s.cmds and s.cmds[-2:] == ["RSET", "QUIT"]


def test_boite_inexistante_550_5_1_1():
    s = FakeSrv({ADDR: (550, b"5.1.1 <contact@garage-exemple.fr>: Recipient address rejected: User unknown in virtual mailbox table")})
    r = run({"mx1.exemple.net": s})
    assert r["result"] == "invalid" and r["code"] == 550
    assert not any(c.startswith("RCPT verif-") for c in s.cmds)                 # inutile de tester le catch-all


@pytest.mark.parametrize("code", [551, 553])
def test_autres_codes_de_boite_inexistante(code):
    assert run({"mx1.exemple.net": FakeSrv({ADDR: (code, b"no such user here")})})["result"] == "invalid"


def test_catch_all():
    s = FakeSrv({ADDR: (250, b"ok"), "random": (250, b"ok")})
    r = run({"mx1.exemple.net": s})
    assert r["result"] == "catch_all"


def test_greylisting_450():
    r = run({"mx1.exemple.net": FakeSrv({ADDR: (450, b"4.2.0 Greylisted, try again later")})})
    assert r["result"] == "unknown" and r["code"] == 450 and r["connected"]


@pytest.mark.parametrize("msg", [b"5.7.1 Service unavailable; client host blocked using Spamhaus", b"554 policy rejection", b"relay access denied",
                                 b"5.1.1 blocked by blocklist (user unknown)"])
def test_blocage_de_politique_est_unknown(msg):
    for code in (550, 554):
        assert run({"mx1.exemple.net": FakeSrv({ADDR: (code, msg)})})["result"] == "unknown"


def test_550_sans_reponse_claire_est_unknown():
    assert run({"mx1.exemple.net": FakeSrv({ADDR: (550, b"Requested action not taken")})})["result"] == "unknown"


def test_mail_from_refuse():
    assert run({"mx1.exemple.net": FakeSrv({}, mail=(550, b"sender rejected"))})["result"] == "unknown"


def test_timeout_en_cours_de_dialogue_puis_second_mx():
    ok = FakeSrv({ADDR: (250, b"ok"), "random": (550, b"5.1.1 no such user")})
    r = run({"mx1.exemple.net": FakeSrv({}, fail_on="rcpt"), "mx2.exemple.net": ok}, hosts=("mx1.exemple.net", "mx2.exemple.net"))
    assert r["result"] == "valid"


def test_port_ferme_ou_timeout_connexion():
    r = run({"mx1.exemple.net": ConnectionRefusedError("refusé"), "mx2.exemple.net": TimeoutError("timed out")},
            hosts=("mx1.exemple.net", "mx2.exemple.net"))
    assert (r["result"], r["code"], r["connected"]) == ("unknown", None, False)


def test_banniere_refusee_est_unknown_connecte():
    r = run({"mx1.exemple.net": smtplib.SMTPConnectError(554, b"5.7.1 your IP is blocked")})
    assert r["result"] == "unknown" and r["connected"] and r["code"] == 554


def test_verify_renvoie_le_resultat(monkeypatch):
    monkeypatch.setattr(mailverify, "check", lambda *a, **k: {"result": "valid"})
    assert mailverify.verify(ADDR, ["mx"], "h", "f@h") == "valid"
