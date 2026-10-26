"""Envoi automatique : composition des e-mails, fenêtre d'envoi, DNS et classement des réponses (sans réseau ni base)."""
from __future__ import annotations

import datetime as dt
from email import message_from_bytes, message_from_string
from email.policy import default as default_policy

import pytest

from worker.local import inbox, maildns, mailer

SENDER = {"name": "Théo Exemple", "company": "Exemple Web", "role": "Développeur web", "phone": "06 12 34 56 78",
          "email": "contact@exemple-web.fr", "website": "exemple-web.fr", "logo_url": "https://exemple-web.fr/logo.png"}
BODY = ("Bonjour,\n\nJe n'ai pas trouvé de site pour votre garage, sauf erreur de ma part.\n\nEst-ce un sujet pour vous ?\n\n"
        "Bien cordialement,\nThéo Exemple\nExemple Web\n06 12 34 56 78\nexemple-web.fr\n\n--\n"
        "Coordonnées trouvées sur des sources publiques. Si vous ne souhaitez plus de message de ma part, dites-le-moi simplement. "
        "En savoir plus : https://exemple-web.fr/mentions-legales.html#prospection")
P = {"id": 1, "email": "contact@garage-exemple.fr", "email_kind": "GENERIC_BUSINESS", "do_not_contact": 0, "draft_subject": "Votre garage",
     "draft_message": BODY, "manager_name": None}


def test_reglage_absent_ou_illisible_desactive_l_envoi():
    assert mailer.settings(None)["enabled"] is False
    assert mailer.settings({"enabled": "true"})["enabled"] is False          # seul un vrai booléen active
    cfg = mailer.settings({"enabled": True, "daily_cap": 500, "start_hour": 18, "end_hour": 9})
    assert cfg["enabled"] and cfg["daily_cap"] == 30 and (cfg["start_hour"], cfg["end_hour"]) == (9, 17)


@pytest.mark.parametrize("when,expected", [
    (dt.datetime(2026, 10, 26, 9, 30, tzinfo=mailer.PARIS), True),          # lundi
    (dt.datetime(2026, 10, 26, 8, 59, tzinfo=mailer.PARIS), False),
    (dt.datetime(2026, 10, 26, 17, 0, tzinfo=mailer.PARIS), False),
    (dt.datetime(2026, 10, 25, 10, 0, tzinfo=mailer.PARIS), False),         # dimanche
    (dt.datetime(2026, 11, 11, 10, 0, tzinfo=mailer.PARIS), False),         # 11 novembre
    (dt.datetime(2026, 4, 6, 10, 0, tzinfo=mailer.PARIS), False),           # lundi de Pâques 2026
    (dt.datetime(2026, 5, 14, 10, 0, tzinfo=mailer.PARIS), False),          # Ascension 2026
])
def test_fenetre_d_envoi(when, expected):
    assert mailer.in_window(when, mailer.settings({"enabled": True})) is expected


def test_minuit_de_paris_en_utc():
    assert mailer.paris_midnight_utc(dt.datetime(2026, 10, 26, 10, 0, tzinfo=dt.timezone.utc)) == dt.datetime(2026, 10, 25, 23, 0)


@pytest.mark.parametrize("change,reason", [
    ({}, None),
    ({"email_kind": "PERSONAL_BUSINESS", "email": "jean.dupont@garage-exemple.fr"}, None),
    ({"email": "garage@gmail.com", "email_kind": "UNCERTAIN"}, "adresse non professionnelle ou incertaine"),
    ({"email": "garage@orange.fr", "email_kind": "GENERIC_BUSINESS"}, "adresse non professionnelle ou incertaine"),
    ({"email": None}, "pas d'adresse e-mail"),
    ({"do_not_contact": 1}, "ne plus contacter"),
    ({"draft_message": None}, "pas de brouillon"),
    ({"draft_message": BODY.replace("\n--\n", "\n")}, "ligne d'opposition absente du brouillon"),
    ({"draft_message": BODY.replace("Théo Exemple\nExemple", "[Votre prénom et nom]\nExemple")}, "brouillon incomplet (nom ou notice manquant dans Réglages)"),
])
def test_eligibilite(change, reason):
    assert mailer.ineligible({**P, **change}) == reason


def test_premier_message_texte_identique_et_html_avec_signature():
    msg = mailer.build_message(from_addr="contact@exemple-web.fr", from_name="Théo Exemple", to=P["email"], subject="Votre garage",
                               text=BODY, sender=SENDER)
    assert msg["Message-ID"].endswith("@exemple-web.fr>") and "In-Reply-To" not in msg
    assert msg["List-Unsubscribe"] == "<mailto:contact@exemple-web.fr?subject=D%C3%A9sinscription>"
    parsed = message_from_string(msg.as_string(), policy=default_policy)
    assert parsed.get_body(("plain",)).get_content().replace("\r\n", "\n").strip() == BODY
    page = parsed.get_body(("html",)).get_content()
    assert 'src="https://exemple-web.fr/logo.png"' in page and 'width="48"' in page and "<strong" in page
    assert 'href="tel:+33612345678"' in page and 'href="https://exemple-web.fr"' in page
    assert page.count("Théo Exemple") == 1                                   # la signature texte est remplacée, pas doublée
    assert "dites-le-moi simplement" in page and "font-size:11px" in page    # opposition présente, en petit


def test_brouillon_modifie_a_la_main_sans_formule_de_fin():
    text = "Bonjour,\n\nUn message écrit autrement.\n\nThéo\n\n--\nSi vous ne souhaitez plus de message, dites-le-moi."
    page = mailer.html_body(text, SENDER)
    assert "Théo" in page and "<table" not in page                        # pas de seconde signature ajoutée
    assert "dites-le-moi" in page


def test_logo_seulement_en_https():
    assert "<img" not in mailer.signature_html({**SENDER, "logo_url": "http://exemple-web.fr/logo.png"})


def test_relances_courtes_en_reponse_au_premier_message():
    sent_at = dt.datetime(2026, 10, 26, 9, 12)                             # lundi, UTC naïf comme en base
    first = {"sent_at": sent_at, "body": BODY, "subject": "Votre garage", "message_id": "<a@exemple-web.fr>"}
    r1 = mailer.followup_body("followup1", P, first, dt.datetime(2026, 10, 29, 10, 0, tzinfo=dt.timezone.utc), "Théo Exemple")
    assert r1.startswith("Bonjour,\n\nJe vous ai écrit lundi au sujet d'un site internet. Est-ce que c'est un sujet pour vous en ce moment ?")
    assert "dites-le-moi simplement" in r1 and "Le lundi 26 octobre 2026 à 10:12, Théo Exemple a écrit :" in r1
    assert "> Je n'ai pas trouvé de site" in r1 and "> Coordonnées trouvées" not in r1
    r2 = mailer.followup_body("followup2", {**P, "manager_name": "Mme Martin"}, first, dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc), "Théo Exemple")
    assert r2.startswith("Bonjour Mme Martin,\n\nJe vous ai écrit la semaine dernière") and "Je ne vous relancerai plus" in r2
    for banned in ("je me permets", "je reviens vers vous", "n'hésitez pas"):
        assert banned not in (r1 + r2).lower()
    msg = mailer.build_message(from_addr="contact@exemple-web.fr", from_name="Théo Exemple", to=P["email"], subject=mailer.reply_subject("Votre garage"),
                               text=r1, sender=SENDER, in_reply_to=first["message_id"])
    assert msg["Subject"] == "Re: Votre garage" and msg["In-Reply-To"] == msg["References"] == "<a@exemple-web.fr>"
    page = mailer.html_body(r1, SENDER)
    assert "<blockquote" in page and page.index("<table") < page.index("<blockquote")
    assert mailer.reply_subject("RE : déjà") == "RE : déjà"


def _txt(records):
    return lambda name: records.get(name, [])


def test_dns_deux_dmarc_bloquent_l_envoi():
    recs = {"exemple-web.fr": ["v=spf1 include:mx.ovh.com ~all"],
            "_dmarc.exemple-web.fr": ["v=DMARC1; p=quarantine; adkim=s; aspf=s", "v=DMARC1; p=none"]}
    r = maildns.check("exemple-web.fr", None, txt=_txt(recs))
    assert not r["ok"] and "2 enregistrements DMARC" in r["problems"][0] and any("DKIM non vérifié" in w for w in r["warnings"])


def test_dns_correct_avec_dkim():
    recs = {"exemple-web.fr": ["v=spf1 include:mx.ovh.com ~all", "google-site-verification=x"],
            "_dmarc.exemple-web.fr": ["v=DMARC1; p=quarantine"], "ovh1._domainkey.exemple-web.fr": ["v=DKIM1; k=rsa; p=MIIB"]}
    assert maildns.check("exemple-web.fr", "ovh1", txt=_txt(recs)) == {"ok": True, "problems": [], "warnings": []}
    assert maildns.check("exemple-web.fr", "absent", txt=_txt(recs))["problems"] == ["aucune clé DKIM sur absent._domainkey.exemple-web.fr"]


def test_dns_injoignable_bloque():
    def boom(name):
        raise RuntimeError("DNS injoignable")
    assert maildns.check("exemple-web.fr", None, txt=boom)["ok"] is False


def _mail(headers: str, body: str = "") -> object:
    return message_from_bytes((headers.strip() + "\nContent-Transfer-Encoding: 8bit\n\n" + body).encode(), policy=default_policy)


@pytest.mark.parametrize("text,expected", [
    ("Bonjour, oui pourquoi pas, appelez-moi jeudi.", "replied"),
    ("Merci de ne plus me contacter.", "opted_out"),
    ("STOP", "opted_out"),
    ("Supprimez mon adresse de votre fichier", "opted_out"),
    ("Non merci, nous avons déjà un site.", "not_interested"),
    ("Ça ne nous intéresse pas.", "not_interested"),
    ("Pas intéressé pour le moment", "not_interested"),
])
def test_resultat_d_une_reponse(text, expected):
    assert inbox.outcome(text) == expected


def test_desinscription_par_le_lien_du_message():
    assert inbox.outcome("", "Désinscription") == "opted_out"


def test_texte_de_reponse_sans_le_message_cite():
    msg = _mail("From: a@garage-exemple.fr\nSubject: Re: Votre garage\nContent-Type: text/plain; charset=utf-8",
                "Oui, rappelez-moi.\n\nLe lun. 26 oct. 2026 à 10:12, Théo Exemple <contact@exemple-web.fr> a écrit :\n> Merci de ne plus me contacter si…")
    assert inbox.reply_text(msg) == "Oui, rappelez-moi." and inbox.outcome(inbox.reply_text(msg)) == "replied"


def test_classement_reponse_auto_et_erreurs():
    assert inbox.classify(_mail("From: a@b.fr\nSubject: Absence du bureau\nAuto-Submitted: auto-replied", "Je suis absent.")) == "auto"
    assert inbox.classify(_mail("From: a@b.fr\nSubject: Re: Votre garage", "Oui")) == "message"
    bounce = _mail("From: MAILER-DAEMON@mx.exemple.net\nSubject: Undelivered Mail\nContent-Type: text/plain",
                   "Final-Recipient: rfc822; contact@garage-exemple.fr\nAction: failed\nStatus: 5.1.1\n")
    assert inbox.classify(bounce) == "bounce"
    delayed = _mail("From: MAILER-DAEMON@mx.exemple.net\nSubject: Delayed Mail\nContent-Type: text/plain", "Action: delayed\nStatus: 4.4.1\n")
    assert inbox.classify(delayed) == "delay"
