"""Commande explicite : vrai assemblage MIME/SMTP, faux transports et base limitée à SELECT."""
from __future__ import annotations

import json
import smtplib
from email import message_from_bytes
from email.policy import default

import pytest

from worker import db
from worker.local import inbox, maildns, mailer, outbox, sendtest


@pytest.fixture
def sandbox(monkeypatch):
    sender = {
        "name": "Camille Exemple", "company": "Studio Exemple", "role": "Développeur web",
        "phone": "0000000000", "email": "contact@example.org", "website": "https://example.org",
        "privacy_url": "https://example.org/confidentialite", "logo_url": "https://example.org/logo.png",
    }
    state = {"sender": sender, "sql": [], "connections": 0, "commits": 0, "smtp": [], "messages": [], "dns": [], "failure": None}
    cfg = {"host": "smtp.example.org", "port": 587, "user": "contact@example.org", "password": "test-password",
           "from": "contact@example.org", "from_name": "", "dkim_selector": "selector-test"}
    monkeypatch.setattr(outbox, "mail_config", lambda: cfg)

    def forbidden(*args, **kwargs):
        pytest.fail("Écriture ou traitement de file / IMAP interdit")

    class ReadOnlyConnection:
        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            assert sql == "SELECT value_json FROM settings WHERE skey=%s LIMIT 1"
            assert params == ("local_sender",)
            state["sql"].append((sql, params))

        def fetchone(self):
            return {"value_json": json.dumps(state["sender"])}

        def commit(self):
            state["commits"] += 1

        def rollback(self):
            pass

        def close(self):
            pass

    def connect(**kwargs):
        state["connections"] += 1
        return ReadOnlyConnection()

    class FakeSMTP(smtplib.SMTP):
        # send_message reste celui de smtplib : on vérifie sa vraie enveloppe.
        def __init__(self, host, port, timeout, context=None):
            state["smtp"].append((host, port, timeout, context))
            self.calls = []
            state["calls"] = self.calls

        def starttls(self, *, context):
            self.calls.append("starttls")

        def login(self, user, password):
            assert (user, password) == (cfg["user"], cfg["password"])
            self.calls.append("login")

        def ehlo_or_helo_if_needed(self):
            pass

        def sendmail(self, from_addr, to_addrs, msg, mail_options=(), rcpt_options=()):
            self.calls.append("sendmail")
            state["messages"].append((from_addr, to_addrs, msg))
            if state["failure"]:
                raise state["failure"]
            return {}

        def quit(self):
            self.calls.append("quit")

    def txt(name):
        state["dns"].append(name)
        return {
            "example.org": ["v=spf1 include:mx.example.org -all"],
            "_dmarc.example.org": ["v=DMARC1; p=quarantine"],
            "selector-test._domainkey.example.org": ["v=DKIM1; p=test-key"],
        }.get(name, [])

    # Le contrôle DNS réel reste exécuté ; seul son transport est remplacé.
    check = maildns.check
    state["txt"] = txt
    monkeypatch.setattr(maildns, "check", lambda domain, selector: check(domain, selector, txt=state["txt"]))
    monkeypatch.setattr(db.pymysql, "connect", connect)
    monkeypatch.setattr(db, "execute", forbidden)
    monkeypatch.setattr(db, "fetch_all", forbidden)
    monkeypatch.setattr(outbox, "run", forbidden)
    monkeypatch.setattr(outbox, "send_one", forbidden)
    monkeypatch.setattr(inbox, "connect", forbidden)
    monkeypatch.setattr(inbox, "poll", forbidden)
    monkeypatch.setattr(outbox.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(outbox.smtplib, "SMTP_SSL", FakeSMTP)
    return cfg, state


@pytest.mark.parametrize("port,from_name", [(587, ""), (465, "Studio configuré")])
def test_unique_recipient_real_headers_signature_no_writes(sandbox, capsys, port, from_name):
    cfg, state = sandbox
    cfg.update(port=port, from_name=from_name)
    assert sendtest.main(["test@example.net"]) == 0
    assert len(state["smtp"]) == len(state["messages"]) == 1
    assert state["smtp"][0][1] == port
    assert state["calls"] == (["starttls"] if port == 587 else []) + ["login", "sendmail", "quit"]
    envelope_from, recipients, raw = state["messages"][0]
    assert envelope_from == cfg["from"]
    assert recipients == ["test@example.net"]
    msg = message_from_bytes(raw, policy=default)
    assert msg["To"].addresses[0].addr_spec == recipients[0]
    assert len(msg["To"].addresses) == 1 and not msg["Cc"] and not msg["Bcc"]
    assert msg["From"].addresses[0].display_name == (from_name or state["sender"]["name"])
    assert msg["Date"] and msg["Message-ID"].endswith("@example.org>")
    assert msg["List-Unsubscribe"] == "<mailto:contact@example.org?subject=D%C3%A9sinscription>"
    assert not msg["In-Reply-To"] and not msg["References"]
    assert msg.get_content_type() == "multipart/alternative"
    text = msg.get_body(("plain",)).get_content().replace("\r\n", "\n")
    assert text.rstrip() == sendtest.sample_body(state["sender"]).rstrip()
    signature = "\n".join(state["sender"][k] for k in ("name", "company", "phone", "email", "website"))
    assert f"{mailer.CLOSING}\n{signature}\n\n--\n" in text
    assert "Atelier des Tilleuls" in text and state["sender"]["privacy_url"] in text
    assert mailer.signature_html(state["sender"]) in msg.get_body(("html",)).get_content()
    assert state["connections"] == state["commits"] == len(state["sql"]) == 1
    assert len(state["dns"]) == 3
    output = capsys.readouterr()
    assert "accepté" in output.out
    assert all(v not in output.out + output.err for v in (cfg["password"], recipients[0], state["sender"]["name"]))


@pytest.mark.parametrize("args", [[], ["a@example.net", "b@example.net"], ["a@example.net,b@example.net"],
                                  ["a@example.net;b@example.net"], ["Nom <a@example.net>"],
                                  ["a@example.net\r\nBcc: b@example.net"], ["invalide"], ["a@localhost"],
                                  [" a@example.net"], ["a..b@example.net"]])
def test_invalid_arguments_no_io(sandbox, capsys, args):
    _, state = sandbox
    with pytest.raises(SystemExit) as exc:
        sendtest.main(args)
    assert exc.value.code == 2
    assert not state["smtp"] and not state["connections"] and not state["dns"]
    output = capsys.readouterr()
    assert "a@example.net" not in output.err and "b@example.net" not in output.err


@pytest.mark.parametrize("failure", ["DMARC", "SPF", "DKIM", "unavailable"])
def test_dns_blocks_before_smtp_or_db(sandbox, capsys, failure):
    _, state = sandbox
    good_txt = state["txt"]

    def txt(name):
        if failure == "unavailable":
            raise RuntimeError("sensitive-server-detail")
        if (failure == "DMARC" and name.startswith("_dmarc.")) or (failure == "SPF" and name == "example.org") or (
            failure == "DKIM" and "._domainkey." in name
        ):
            return []
        return good_txt(name)

    state["txt"] = txt
    assert sendtest.main(["test@example.net"]) == 1
    assert not state["smtp"] and not state["connections"] and not state["sql"]
    output = capsys.readouterr()
    assert "Aucun envoi SMTP" in output.err
    assert (failure if failure != "unavailable" else "indisponible") in output.err
    assert "sensitive-server-detail" not in output.err


def test_missing_smtp_config_no_io(sandbox, capsys):
    cfg, state = sandbox
    cfg["password"] = ""
    assert sendtest.main(["test@example.net"]) == 1
    assert not state["smtp"] and not state["connections"] and not state["dns"]
    assert "Configuration SMTP incomplète" in capsys.readouterr().err


def test_multiple_dmarc_blocks_smtp(sandbox, capsys):
    _, state = sandbox
    good_txt = state["txt"]
    state["txt"] = lambda name: ["v=DMARC1; p=none", "v=DMARC1; p=quarantine"] if name.startswith("_dmarc.") else good_txt(name)
    assert sendtest.main(["test@example.net"]) == 1
    assert not state["smtp"] and not state["connections"]
    assert "DMARC absent ou multiple" in capsys.readouterr().err


def test_db_failure_no_smtp_and_no_sensitive_output(sandbox, monkeypatch, capsys):
    _, state = sandbox

    def broken_connection(**kwargs):
        raise RuntimeError("sensitive-server-detail test-password")

    monkeypatch.setattr(db.pymysql, "connect", broken_connection)
    assert sendtest.main(["test@example.net"]) == 1
    assert not state["smtp"]
    output = capsys.readouterr()
    assert "sensitive-server-detail" not in output.err and "test-password" not in output.err


@pytest.mark.parametrize("sender", [{}, {"name": "  ", "privacy_url": "https://example.org"}])
def test_incomplete_signature_no_smtp_or_writes(sandbox, capsys, sender):
    _, state = sandbox
    state["sender"] = sender
    assert sendtest.main(["test@example.net"]) == 1
    assert not state["smtp"] and len(state["sql"]) == 1
    assert "Signature incomplète" in capsys.readouterr().err


def test_smtp_failure_no_retry_or_writes_and_no_sensitive_output(sandbox, capsys):
    cfg, state = sandbox
    state["failure"] = smtplib.SMTPDataError(550, b"test-password test@example.net sensitive-server-detail")
    assert sendtest.main(["test@example.net"]) == 1
    assert len(state["messages"]) == len(state["smtp"]) == len(state["sql"]) == 1
    output = capsys.readouterr()
    assert "aucune nouvelle tentative" in output.err
    assert not output.out
    assert all(v not in output.err for v in (cfg["password"], "test@example.net", "sensitive-server-detail"))
