"""Envoi automatique sur un VRAI MySQL, avec un faux serveur SMTP et une fausse boîte IMAP : premier e-mail, relances J+3 / J+7 en réponse,
abandon à J+14, arrêt dès une réponse, refus -> « ne plus contacter », adresse invalide, plafond, heures, DNS. Rien ne part tant que c'est désactivé.
Ce que ça ne prouve pas : qu'OVH accepte ces messages ni qu'ils arrivent en boîte de réception (à vérifier par un envoi réel à soi-même)."""
from __future__ import annotations

import datetime as dt
import json
import smtplib
from email import message_from_bytes
from email.policy import default as default_policy

import pytest

from worker import db
from worker.local import outbox, sitefinder

SENDER = {"name": "Théo Exemple", "company": "Exemple Web", "role": "Développeur web", "phone": "06 12 34 56 78",
          "email": "contact@exemple-web.fr", "website": "exemple-web.fr", "logo_url": "https://exemple-web.fr/logo.png"}
BODY = ("Bonjour,\n\nJe n'ai pas trouvé de site pour votre garage, sauf erreur de ma part.\n\nEst-ce un sujet pour vous ?\n\n"
        "Bien cordialement,\nThéo Exemple\nExemple Web\n06 12 34 56 78\nexemple-web.fr\n\n--\n"
        "Coordonnées trouvées sur des sources publiques. Si vous ne souhaitez plus de message de ma part, dites-le-moi simplement.")

PARIS = dt.timezone(dt.timedelta(hours=1))                                     # novembre : UTC+1
T0 = dt.datetime(2026, 11, 2, 10, 0, tzinfo=PARIS)                             # lundi 10 h
DNS_OK = {"exemple-web.fr": ["v=spf1 include:mx.ovh.com ~all"], "_dmarc.exemple-web.fr": ["v=DMARC1; p=quarantine"]}
NO_MAIL: set[str] = set()                                                      # domaines de destinataires sans MX ni A (remplis par les tests)


def fake_mx(name, rtype):
    """DNS des destinataires : un MX partout, sauf les domaines de NO_MAIL ; « panne.example » ne répond pas."""
    if name == "panne.example":
        raise RuntimeError("DNS injoignable (test)")
    return [] if name in NO_MAIL else (["10 mx.exemple.net."] if rtype == "MX" else [])


class FakeSMTP:
    def __init__(self):
        self.sent: list = []
        self.error: Exception | None = None

    def __call__(self, msg, cfg):
        if self.error:
            raise self.error
        self.sent.append(msg)


class FakeImap:
    """Boîte IMAP en mémoire. Refuse toute écriture dans la boîte de réception : seule la copie dans « Envoyés » est permise."""

    def __init__(self):
        self.messages: dict[int, bytes] = {}
        self.appended: list[tuple[str, bytes]] = []
        self.readonly = None

    def __call__(self, cfg):
        return self

    def add(self, raw: str) -> None:
        self.messages[max(self.messages, default=100) + 1] = raw.encode()

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        return code, [b"777"]

    def uid(self, cmd, *args):
        assert cmd in ("search", "fetch"), cmd                                 # jamais STORE / COPY / EXPUNGE
        if cmd == "search":
            crit = args[1]
            ids = sorted(self.messages)
            if crit.startswith("UID "):
                lo = int(crit[4:].split(":")[0])
                ids = [u for u in ids if u >= lo] or ids[-1:]                   # comme un vrai serveur : « n:* » renvoie au moins le dernier
            return "OK", [" ".join(map(str, ids)).encode()]
        assert "BODY.PEEK" in args[1]                                          # lecture sans marquer lu
        return "OK", [(f"{args[0]} (UID {args[0]} BODY[]<0> {{1}}".encode(), self.messages[int(args[0])]), b")"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent Items"']

    def append(self, box, flags, date, raw):
        self.appended.append((box, raw))
        return "OK", [b""]

    def logout(self):
        pass


@pytest.fixture
def mail(env, conn, monkeypatch, telegram):
    for k, v in {"LOCAL_SMTP_HOST": "smtp.exemple.net", "LOCAL_SMTP_USER": "contact@exemple-web.fr", "LOCAL_SMTP_PASSWORD": "mot-de-passe-de-test"}.items():
        monkeypatch.setenv(k, v)
    for k in ("LOCAL_MAIL_FROM", "LOCAL_MAIL_FROM_NAME", "LOCAL_IMAP_HOST", "LOCAL_DKIM_SELECTOR"):
        monkeypatch.delenv(k, raising=False)
    for t in ("local_emails", "local_mail_state"):
        db.execute(conn, f"DELETE FROM {t}")
    db.execute(conn, "DELETE FROM settings WHERE skey IN ('local_auto_send', 'local_sender')")
    uid = db.fetch_one(conn, "SELECT id FROM users LIMIT 1")["id"]
    db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) VALUES (%s, 'local_sender', %s)", (uid, json.dumps(SENDER)))
    smtp, imap = FakeSMTP(), FakeImap()
    NO_MAIL.clear()
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: [])     # aucune résolution réelle des domaines devinés

    class M:
        def enable(self, **cfg):
            db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) VALUES (%s, 'local_auto_send', %s) ON DUPLICATE KEY UPDATE value_json=VALUES(value_json)",
                       (uid, json.dumps({"enabled": True, **cfg})))

        def run(self, now, dns=DNS_OK, fetch=None):
            return outbox.run(conn, now=now.astimezone(dt.timezone.utc), send=smtp, imap=imap, dns=lambda n: dns.get(n, []), mx_query=fake_mx,
                              site_fetch=fetch or (lambda url: None))

    m = M()
    m.smtp, m.imap, m.tg = smtp, imap, telegram
    return m


def add(conn, siret: str, email: str = "contact@garage-exemple.fr", kind: str = "GENERIC_BUSINESS", queued: bool = True, minutes: int = 0) -> int:
    """Fiche COMPLÈTE (métier connu, site confirmé, traitement terminé) avec un premier message en file."""
    db.execute(conn, """INSERT INTO local_prospects (siret, siren, fingerprint, company_name, trade_name, city, email, email_kind, status, category,
                        activity_key, activity_label, website_status, website_url, pipeline_stage, draft_subject, draft_message, auto_state, auto_queued_at)
                        VALUES (%s,%s,%s,'GARAGE EXEMPLE SARL','Garage Exemple','Troyes',%s,%s,'QUALIFIED','A_CONTACTER','garages','Garages','CONFIRMED',
                        'https://www.garage-exemple.fr/','DONE','Votre garage',%s,%s, UTC_TIMESTAMP() - INTERVAL %s MINUTE)""",
               (siret, siret[:9], f"fp{siret}", email, kind, BODY, "queued" if queued else None, 100 - minutes))
    return db.fetch_one(conn, "SELECT id FROM local_prospects WHERE siret=%s", (siret,))["id"]


def row(conn, pid):
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))


def test_desactive_par_defaut_rien_ne_part(mail, conn):
    pid = add(conn, "10000000000011")
    assert mail.run(T0)["status"] == "disabled"
    assert mail.smtp.sent == [] and row(conn, pid)["auto_state"] == "queued"


def test_sequence_complete_premier_message_relances_abandon(mail, conn):
    pid = add(conn, "10000000000011")
    mail.enable()
    r = mail.run(T0)
    assert (r["status"], r["kind"]) == ("sent", "first")
    first = mail.smtp.sent[0]
    assert first["To"] == "contact@garage-exemple.fr" and first["Subject"] == "Votre garage" and first["From"] == "Théo Exemple <contact@exemple-web.fr>"
    p = row(conn, pid)
    assert (p["status"], p["auto_state"], p["followups"]) == ("CONTACTED", "active", 0) and p["contacted_at"] == dt.datetime(2026, 11, 2, 9, 0)
    assert [b for b, _ in mail.imap.appended] == ['"Sent Items"']                # copie dans Envoyés, pour garder le fil complet
    assert mail.run(T0 + dt.timedelta(minutes=10))["status"] == "idle"         # J+0 : rien d'autre à envoyer
    assert mail.run(T0 + dt.timedelta(days=2))["status"] == "idle"             # mercredi : trop tôt pour relancer

    assert mail.run(T0 + dt.timedelta(days=5))["status"] == "hours"            # samedi : rien ne part le week-end
    assert mail.run(T0 + dt.timedelta(days=3))["kind"] == "followup1"          # jeudi
    r1 = mail.smtp.sent[1]
    assert r1["Subject"] == "Re: Votre garage" and r1["In-Reply-To"] == first["Message-ID"]
    assert "Je vous ai écrit lundi" in r1.get_body(("plain",)).get_content()
    assert row(conn, pid)["followups"] == 1
    assert mail.run(T0 + dt.timedelta(days=4))["status"] == "idle"             # vendredi : pas encore J+7
    assert mail.run(T0 + dt.timedelta(days=7))["kind"] == "followup2"          # lundi suivant
    assert "Je ne vous relancerai plus" in mail.smtp.sent[2].get_body(("plain",)).get_content()
    assert mail.run(T0 + dt.timedelta(days=10))["status"] == "idle"            # plus rien après la 2e relance
    assert len(mail.smtp.sent) == 3

    r = mail.run(T0 + dt.timedelta(days=14))
    assert r["abandoned"] == 1
    p = row(conn, pid)
    assert (p["status"], p["response_status"], p["auto_state"]) == ("LOST", "NO_ANSWER", "abandoned")
    kinds = [e["kind"] for e in db.fetch_all(conn, "SELECT kind FROM local_emails WHERE prospect_id=%s ORDER BY id", (pid,))]
    assert kinds == ["first", "followup1", "followup2"]


def test_une_reponse_arrete_les_relances(mail, conn):
    pid = add(conn, "10000000000011")
    mail.enable()
    mail.run(T0)
    mid = mail.smtp.sent[0]["Message-ID"]
    mail.imap.add(f"From: Garage <patron@garage-exemple.fr>\nSubject: Re: Votre garage\nIn-Reply-To: {mid}\nContent-Type: text/plain; charset=utf-8\n"
                  f"Content-Transfer-Encoding: 8bit\n\nBonjour, oui, appelez-moi jeudi matin.\n")
    r = mail.run(T0 + dt.timedelta(days=3))
    assert r["inbox"]["replied"] == 1 and r["status"] == "idle" and len(mail.smtp.sent) == 1
    p = row(conn, pid)
    assert (p["status"], p["response_status"], p["auto_state"]) == ("REPLIED", "REPLIED", "replied")
    assert mail.imap.readonly is True and any("a répondu" in t for t in mail.tg.texts)
    assert db.fetch_one(conn, "SELECT imap_last_uid FROM local_mail_state WHERE id=1")["imap_last_uid"] == 101
    assert mail.run(T0 + dt.timedelta(days=3, minutes=10))["inbox"]["read"] == 0  # le même message n'est pas relu


def test_refus_ajoute_a_ne_plus_contacter(mail, conn):
    pid = add(conn, "10000000000011")
    mail.enable()
    mail.run(T0)
    mail.imap.add("From: contact@garage-exemple.fr\nSubject: RE: Votre garage\nContent-Type: text/plain; charset=utf-8\n"
                  "Content-Transfer-Encoding: 8bit\n\nMerci de ne plus nous contacter.\n")
    assert mail.run(T0 + dt.timedelta(days=3))["inbox"]["opted_out"] == 1
    p = row(conn, pid)
    assert (p["do_not_contact"], p["status"], p["auto_state"]) == (1, "DO_NOT_CONTACT", "opted_out")
    assert db.fetch_one(conn, "SELECT email FROM local_do_not_contact WHERE siret='10000000000011'")["email"] == "contact@garage-exemple.fr"
    assert len(mail.smtp.sent) == 1


def test_adresse_invalide_et_reponse_automatique(mail, conn):
    pid = add(conn, "10000000000011")
    mail.enable()
    mail.run(T0)
    mail.imap.add("From: contact@garage-exemple.fr\nSubject: Absence du bureau\nAuto-Submitted: auto-replied\n\nJe suis absent jusqu'au 9.\n")
    mail.imap.add("From: MAILER-DAEMON@mx.exemple.net\nSubject: Undelivered Mail Returned to Sender\nContent-Type: text/plain\n\n"
                  "Final-Recipient: rfc822; contact@garage-exemple.fr\nAction: failed\nStatus: 5.1.1\n")
    r = mail.run(T0 + dt.timedelta(days=3))
    assert r["inbox"] == {"read": 2, "bounced": 1} and r["status"] == "idle" and len(mail.smtp.sent) == 1
    assert row(conn, pid)["auto_state"] == "bounced"
    assert db.fetch_one(conn, "SELECT status FROM local_emails WHERE prospect_id=%s", (pid,))["status"] == "bounced"


def test_plafond_heures_et_adresses_ecartees(mail, conn):
    a = add(conn, "10000000000011", minutes=0)
    gmail = add(conn, "20000000000011", email="garage.exemple@gmail.com", kind="UNCERTAIN", minutes=1)
    c = add(conn, "30000000000011", email="contact@autre-exemple.fr", minutes=2)
    mail.enable(daily_cap=1, personal_emails=False)
    assert mail.run(T0.replace(hour=8))["status"] == "hours"
    assert mail.run(T0)["status"] == "sent" and row(conn, a)["auto_state"] == "active"
    assert mail.run(T0 + dt.timedelta(minutes=10))["status"] == "cap"
    assert mail.run(T0 + dt.timedelta(days=1))["status"] == "sent"            # mardi : la gmail est écartée, la suivante part
    assert row(conn, gmail)["auto_state"] == "stopped" and row(conn, c)["auto_state"] == "active"
    assert [m["To"] for m in mail.smtp.sent] == ["contact@garage-exemple.fr", "contact@autre-exemple.fr"]


def test_dns_incorrect_bloque_et_erreur_smtp_reessayee(mail, conn):
    pid = add(conn, "10000000000011")
    mail.enable()
    bad = {**DNS_OK, "_dmarc.exemple-web.fr": ["v=DMARC1; p=quarantine", "v=DMARC1; p=none"]}
    assert mail.run(T0, dns=bad)["status"] == "dns" and mail.smtp.sent == []
    state = db.fetch_one(conn, "SELECT checks FROM local_mail_state WHERE id=1")
    assert "2 enregistrements DMARC" in json.loads(state["checks"])["dns"]["problems"][0]
    mail.smtp.error = smtplib.SMTPAuthenticationError(535, b"authentication failed")
    assert mail.run(T0 + dt.timedelta(hours=6), dns=DNS_OK)["status"] == "error"   # DNS revérifié (6 h), puis échec d'authentification
    assert row(conn, pid)["auto_state"] == "queued"                                # rien de perdu : réessayé au passage suivant
    err = db.fetch_one(conn, "SELECT last_error FROM local_mail_state WHERE id=1")["last_error"]
    assert "mot de passe lu : 20 caractères" in err and "mot-de-passe-de-test" not in err   # longueur seulement, jamais la valeur
    mail.smtp.error = None
    assert mail.run(T0 + dt.timedelta(hours=6, minutes=10))["status"] == "sent"
    assert db.fetch_one(conn, "SELECT last_error FROM local_mail_state WHERE id=1")["last_error"] is None   # erreur effacée par l'envoi réussi


def test_premier_message_jamais_deux_fois_a_la_meme_adresse(mail, conn):
    add(conn, "10000000000011")
    dup = add(conn, "10000000000022", minutes=1)                               # deux établissements, même adresse
    mail.enable()
    mail.run(T0)
    mail.run(T0 + dt.timedelta(minutes=10))
    assert len(mail.smtp.sent) == 1 and row(conn, dup)["auto_state"] == "stopped"


def test_message_parti_lisible(mail, conn):
    add(conn, "10000000000011")
    mail.enable()
    mail.run(T0)
    parsed = message_from_bytes(mail.smtp.sent[0].as_bytes(), policy=default_policy)
    assert "Théo Exemple" in parsed.get_body(("html",)).get_content() and parsed["List-Unsubscribe"]


def test_adresse_perso_envoyee_par_defaut(mail, conn):
    pid = add(conn, "20000000000011", email="garage.exemple@gmail.com", kind="UNCERTAIN")
    mail.enable()
    assert mail.run(T0)["status"] == "sent" and row(conn, pid)["auto_state"] == "active"
    assert mail.smtp.sent[0]["To"] == "garage.exemple@gmail.com"


def test_message_sans_site_efface_si_le_site_est_connu_depuis(mail, conn):
    pid = add(conn, "10000000000011")                                          # site confirmé (saisi depuis), brouillon « sans site » resté
    db.execute(conn, "UPDATE local_prospects SET draft_kind='ai_sans_site' WHERE id=%s", (pid,))
    mail.enable()
    assert mail.run(T0)["status"] == "idle" and mail.smtp.sent == []
    p = row(conn, pid)
    assert p["draft_message"] is None and p["auto_state"] is None and mail.tg.texts == []    # à rédiger de nouveau, aucune alerte


def test_domaine_sans_serveur_de_messagerie_adresse_invalide(mail, conn):
    pid = add(conn, "10000000000011", email="contact@domaine-mort.fr")
    NO_MAIL.add("domaine-mort.fr")
    ok = add(conn, "30000000000011", email="contact@autre-exemple.fr", minutes=1)
    mail.enable()
    r = mail.run(T0)
    assert r["status"] == "sent" and [m["To"] for m in mail.smtp.sent] == ["contact@autre-exemple.fr"]   # la suivante part au même passage
    p = row(conn, pid)
    assert p["auto_state"] == "bounced" and '"email_invalid"' in p["provenance"] and "domaine-mort.fr" in p["provenance"]
    assert row(conn, ok)["auto_state"] == "active" and not any("domaine-mort" in t for t in mail.tg.texts)   # aucune alerte
    checks = json.loads(db.fetch_one(conn, "SELECT checks FROM local_mail_state WHERE id=1")["checks"])
    assert "autre-exemple.fr" in checks["mx"] and "domaine-mort.fr" not in checks["mx"]       # seul un résultat positif est gardé


def test_dns_du_destinataire_injoignable_envoi_repousse(mail, conn):
    pid = add(conn, "10000000000011", email="contact@panne.example")
    mail.enable()
    assert mail.run(T0)["status"] == "deferred" and mail.smtp.sent == []
    p = row(conn, pid)
    assert p["auto_state"] == "queued" and '"retry_at"' in p["provenance"]
    assert mail.run(T0 + dt.timedelta(minutes=2))["status"] == "idle"         # pas de nouvel essai avant PRESEND_RETRY


def test_metier_inconnu_ou_fiche_incomplete_jamais_envoyee(mail, conn):
    a = add(conn, "10000000000011")
    db.execute(conn, "UPDATE local_prospects SET activity_label=NULL, activity_key=NULL WHERE id=%s", (a,))
    b = add(conn, "20000000000011", email="contact@garage-deux.fr", minutes=1)
    db.execute(conn, "UPDATE local_prospects SET website_status='UNCERTAIN' WHERE id=%s", (b,))      # site à trancher
    mail.enable()
    assert mail.run(T0)["status"] == "idle" and mail.smtp.sent == []
    assert row(conn, a)["auto_state"] == "queued" and row(conn, b)["auto_state"] == "queued"        # restent en file, en attente
    assert outbox.waiting(row(conn, a), True, T0) == "fiche incomplète : métier inconnu (activité non classée)"
    db.execute(conn, "UPDATE local_prospects SET activity_label='Garages' WHERE id=%s", (a,))          # complétée : elle part
    assert mail.run(T0 + dt.timedelta(minutes=2))["status"] == "sent" and mail.smtp.sent[0]["To"] == "contact@garage-exemple.fr"


def test_boite_lue_toutes_les_10_min_et_toujours_avant_une_relance(mail, conn):
    add(conn, "10000000000011")
    mail.enable()
    t = T0.astimezone(dt.timezone.utc).replace(tzinfo=None)
    db.execute(conn, "INSERT INTO local_mail_state (id, imap_at) VALUES (1, %s) ON DUPLICATE KEY UPDATE imap_at=VALUES(imap_at)", (t - dt.timedelta(minutes=1),))
    r = mail.run(T0)
    assert r["status"] == "sent" and "inbox" not in r                                # lue il y a 1 min : pas relue pour un premier message
    db.execute(conn, "UPDATE local_mail_state SET imap_at=%s WHERE id=1", (t + dt.timedelta(days=3) - dt.timedelta(minutes=1),))
    assert "inbox" in mail.run(T0 + dt.timedelta(days=3))                            # relance due : boîte relue d'abord
