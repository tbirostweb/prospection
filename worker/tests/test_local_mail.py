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
    assert cfg["enabled"] and cfg["daily_cap"] == 30 and (cfg["start_hour"], cfg["end_hour"]) == (10, 16)
    assert (cfg["pause_start"], cfg["pause_end"], cfg["min_gap_min"]) == (12, 13, 60)               # défauts : pause 12 h-13 h, 1 e-mail par heure
    assert mailer.settings({"min_gap_min": 5})["min_gap_min"] == 15 and mailer.settings({"min_gap_min": 999})["min_gap_min"] == 240
    assert mailer.settings({"min_gap_min": "x"})["min_gap_min"] == 60
    assert mailer.settings({"pause_start": 11, "pause_end": 14})["pause_end"] == 14


@pytest.mark.parametrize("when,expected", [
    (dt.datetime(2026, 10, 26, 10, 30, tzinfo=mailer.PARIS), True),         # lundi
    (dt.datetime(2026, 10, 26, 9, 59, tzinfo=mailer.PARIS), False),
    (dt.datetime(2026, 10, 26, 11, 59, tzinfo=mailer.PARIS), True),
    (dt.datetime(2026, 10, 26, 12, 0, tzinfo=mailer.PARIS), False),         # pause déjeuner 12 h-13 h
    (dt.datetime(2026, 10, 26, 12, 59, tzinfo=mailer.PARIS), False),
    (dt.datetime(2026, 10, 26, 13, 0, tzinfo=mailer.PARIS), True),
    (dt.datetime(2026, 10, 26, 15, 59, tzinfo=mailer.PARIS), True),
    (dt.datetime(2026, 10, 26, 16, 0, tzinfo=mailer.PARIS), False),
    (dt.datetime(2026, 10, 25, 10, 0, tzinfo=mailer.PARIS), False),         # dimanche
    (dt.datetime(2026, 11, 11, 10, 0, tzinfo=mailer.PARIS), False),         # 11 novembre
    (dt.datetime(2026, 4, 6, 10, 0, tzinfo=mailer.PARIS), False),           # lundi de Pâques 2026
    (dt.datetime(2026, 5, 14, 10, 0, tzinfo=mailer.PARIS), False),          # Ascension 2026
])
def test_fenetre_d_envoi(when, expected):
    assert mailer.in_window(when, mailer.settings({"enabled": True})) is expected


def test_pause_incoherente_ignoree():
    h = lambda hour: dt.datetime(2026, 10, 26, hour, 0, tzinfo=mailer.PARIS)
    cfg = mailer.settings({"enabled": True, "start_hour": 13, "end_hour": 18})            # pause 12 h-13 h avant le début : ignorée
    assert mailer.in_window(h(13), cfg) and mailer.in_window(h(17), cfg)
    cfg = mailer.settings({"enabled": True, "pause_start": 14, "pause_end": 13})          # pause vide : ignorée
    assert mailer.in_window(h(14), cfg) and mailer.in_window(h(12), cfg)
    cfg = mailer.settings({"enabled": True, "pause_start": 15, "pause_end": 20})          # déborde de la plage : ignorée
    assert mailer.in_window(h(15), cfg)
    cfg = mailer.settings({"enabled": True, "pause_start": 14, "pause_end": 15})
    assert not mailer.in_window(h(14), cfg) and mailer.in_window(h(12), cfg)


def test_intervalle_minimum_entre_deux_envois():
    cfg, now = mailer.settings({"enabled": True}), dt.datetime(2026, 10, 26, 10, 0, tzinfo=mailer.PARIS)
    utc = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    assert mailer.gap_wait(None, None, now, cfg) is False                                  # aucun envoi encore
    jitter = (5 * 7919) % 11                                                               # décalage dérivé de l'id 5, entre 0 et 10 min
    assert 0 <= jitter <= 10
    limit = dt.timedelta(minutes=60 + jitter)
    assert mailer.gap_wait(utc - limit + dt.timedelta(seconds=1), 5, now, cfg) is True     # un peu trop tôt
    assert mailer.gap_wait(utc - limit, 5, now, cfg) is False                              # intervalle atteint
    assert mailer.gap_wait(utc - dt.timedelta(minutes=20), 5, now, cfg) is True
    assert mailer.gap_wait(utc - dt.timedelta(minutes=71), 5, now, cfg) is False           # 60 + 10 au plus
    assert mailer.gap_wait(utc - dt.timedelta(minutes=26), 5, now, mailer.settings({"min_gap_min": 15})) is False
    assert {(i * 7919) % 11 for i in range(1, 40)} == set(range(11))                        # tous les décalages de 0 à 10 min se rencontrent


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


@pytest.mark.parametrize("change,reason", [
    ({"email": "garage@gmail.com", "email_kind": "UNCERTAIN"}, None),
    ({"email": "garage@orange.fr", "email_kind": "GENERIC_BUSINESS"}, None),
    ({"email": "garage@gmail.com", "email_kind": None}, "adresse non vérifiée"),
    ({"do_not_contact": 1}, "ne plus contacter"),
])
def test_eligibilite_adresses_perso_acceptees(change, reason):
    assert mailer.ineligible({**P, **change}, personal=True) == reason
    assert mailer.settings(None)["personal_emails"] is True and mailer.settings({"personal_emails": False})["personal_emails"] is False


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


def test_salutation_jamais_de_nom():
    assert mailer.greeting({"manager_name": "Nicolas George (George)"}) == "Bonjour,"
    assert mailer.greeting({"manager_name": "X", "manager_civility": "M"}) == "Bonjour Monsieur,"
    assert mailer.greeting({"manager_civility": "Mme"}) == "Bonjour Madame,"


def test_logo_svg_ico_ignores():
    assert "<img" not in mailer.signature_html({**SENDER, "logo_url": "https://exemple-web.fr/logo.svg"})
    assert "<img" in mailer.signature_html({**SENDER, "logo_url": "https://exemple-web.fr/favicon.png"})


def test_logo_en_texte_sans_image():
    page = mailer.signature_html({**SENDER, "logo_text": "BW."})
    assert "<img" not in page and f'BW<span style="color:{mailer.ACCENT}">.</span>' in page and 'bgcolor="#EFEBE4"' in page


def test_logo_seulement_en_https():
    assert "<img" not in mailer.signature_html({**SENDER, "logo_url": "http://exemple-web.fr/logo.png"})


def test_relances_courtes_en_reponse_au_premier_message():
    sent_at = dt.datetime(2026, 10, 26, 9, 12)                             # lundi, UTC naïf comme en base
    first = {"sent_at": sent_at, "body": BODY, "subject": "Votre garage", "message_id": "<a@exemple-web.fr>"}
    r1 = mailer.followup_body("followup1", P, first, dt.datetime(2026, 10, 29, 10, 0, tzinfo=dt.timezone.utc), "Théo Exemple")
    assert r1.startswith("Bonjour,\n\nJe vous ai écrit lundi au sujet d'un site internet. Est-ce que c'est un sujet pour vous en ce moment ?")
    assert "dites-le-moi simplement" in r1 and "Le lundi 26 octobre 2026 à 10:12, Théo Exemple a écrit :" in r1
    assert "> Je n'ai pas trouvé de site" in r1 and "> Coordonnées trouvées" not in r1
    r2 = mailer.followup_body("followup2", {**P, "manager_name": "Nicolas George (George)"}, first, dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc), "Théo Exemple")
    assert r2.startswith("Bonjour,\n\nJe vous ai écrit la semaine dernière") and "quelques-unes de mes réalisations" in r2 and "Je ne vous relancerai plus" in r2
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
    ("Bonjour, oui pourquoi pas, appelez-moi jeudi.", "interested"),
    ("Oui, ça m'intéresse, pouvez-vous m'envoyer un devis ?", "interested"),
    ("Bonjour, rappelez-moi la semaine prochaine.", "interested"),
    ("Quand êtes-vous disponible pour un rendez-vous ?", "interested"),
    ("Pas pour le moment, revenez vers moi dans quelques mois.", "later"),
    ("Plus tard, nous refaisons notre activité l'année prochaine.", "later"),
    ("Oui mais plus tard, après l'été.", "later"),
    ("Non, pas intéressé, merci.", "not_interested"),
    ("Appelez-moi, mais je n'ai pas de budget", "replied"),                      # négation : doute -> neutre
    ("Je ne suis pas disponible jeudi.", "replied"),
    ("Merci pour votre message.", "replied"),
    ("D'où tenez-vous mon adresse ?", "replied"),
    ("Oui. " + "Bla bla. " * 80, "replied"),                                     # long message : on ne devine pas
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


def test_l_objet_ne_fait_pas_l_interet():
    assert inbox.outcome("Merci.", "Re: Un devis pour votre site ?") == "replied"
    assert inbox.outcome("STOP", "Re: devis") == "opted_out"


@pytest.mark.parametrize("start,months,expected", [
    (dt.date(2026, 10, 30), 3, dt.date(2027, 1, 30)), (dt.date(2026, 11, 30), 3, dt.date(2027, 2, 28)),
    (dt.date(2026, 12, 15), 3, dt.date(2027, 3, 15)), (dt.date(2026, 5, 31), 1, dt.date(2026, 6, 30)),
])
def test_rappel_dans_trois_mois(start, months, expected):
    assert inbox.add_months(start, months) == expected


def test_texte_de_reponse_sans_le_message_cite():
    msg = _mail("From: a@garage-exemple.fr\nSubject: Re: Votre garage\nContent-Type: text/plain; charset=utf-8",
                "Oui, rappelez-moi.\n\nLe lun. 26 oct. 2026 à 10:12, Théo Exemple <contact@exemple-web.fr> a écrit :\n> Merci de ne plus me contacter si…")
    assert inbox.reply_text(msg) == "Oui, rappelez-moi." and inbox.outcome(inbox.reply_text(msg)) == "interested"   # la citation (« ne plus me contacter ») ne compte pas


def test_classement_reponse_auto_et_erreurs():
    assert inbox.classify(_mail("From: a@b.fr\nSubject: Absence du bureau\nAuto-Submitted: auto-replied", "Je suis absent.")) == "auto"
    assert inbox.classify(_mail("From: a@b.fr\nSubject: Re: Votre garage", "Oui")) == "message"
    bounce = _mail("From: MAILER-DAEMON@mx.exemple.net\nSubject: Undelivered Mail\nContent-Type: text/plain",
                   "Final-Recipient: rfc822; contact@garage-exemple.fr\nAction: failed\nStatus: 5.1.1\n")
    assert inbox.classify(bounce) == "bounce"
    delayed = _mail("From: MAILER-DAEMON@mx.exemple.net\nSubject: Delayed Mail\nContent-Type: text/plain", "Action: delayed\nStatus: 4.4.1\n")
    assert inbox.classify(delayed) == "delay"


def test_adresses_masquees_et_cloudflare_lues_sur_le_site():
    from worker.local import htmlinfo, snippets
    cf = "a5" + bytes(x ^ 0xa5 for x in b"devis@garage-martin.fr").hex()
    page = htmlinfo.parse(f"""<html><body><p>Écrivez-nous : contact [at] garage-martin.fr ou jean (arobase) garage-martin (point) fr.</p>
        <p>Rendez-vous at 10h, prix 10.50 €.</p><a href="/cdn-cgi/l/email-protection#{cf}">e-mail</a>
        <span class="__cf_email__" data-cfemail="{cf}">[email protected]</span><a href="mailto:info%40garage-martin.fr">écrire</a></body></html>""",
        "https://garage-martin.fr/contact")
    found = dict(page.email_points())
    assert {"contact@garage-martin.fr", "jean@garage-martin.fr", "devis@garage-martin.fr", "info@garage-martin.fr"} <= set(found)
    assert found["devis@garage-martin.fr"] == "mailto" and found["info@garage-martin.fr"] == "mailto"
    assert not any("rendez" in m or m.startswith("10") for m in found)
    assert snippets.email_query({"trade_name": "Garage Martin", "city": "Troyes"}) == '"Garage Martin" Troyes email'


def test_recherche_email_pas_refaite_avant_30_jours():
    import json
    import time
    from worker.local import runner
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    assert runner.email_recently_searched({"provenance": json.dumps({"email_search": {"checked_at": now}})}) is True
    assert runner.email_recently_searched({"provenance": {"email_search": {"checked_at": "2026-01-01T00:00:00Z"}}}) is False
    assert runner.email_recently_searched({"provenance": None}) is False


def _apply_reply(monkeypatch, body, send):
    from email.message import EmailMessage
    from worker import db, notify
    p = {"id": 7, "trade_name": "Garage Dupont", "city": "Troyes", "status": "CONTACTED", "remind_at": None, "deal_stage": None}
    monkeypatch.setattr(db, "fetch_one", lambda conn, sql, args=(): p)
    monkeypatch.setattr(db, "execute", lambda *a, **k: None)
    monkeypatch.setattr(notify, "send_text", send)
    monkeypatch.setattr(inbox, "APP_URL", "https://app.test")
    m = EmailMessage()
    m["Subject"] = "Re: votre site"
    m.set_content(body)
    return inbox.apply(None, {"id": 1, "prospect_id": 7, "to_email": "a@b.fr"}, "message", m)


def test_reponse_interessee_alerte_violette(monkeypatch):
    sent = []
    assert _apply_reply(monkeypatch, "Oui, rappelez-moi.", lambda t, *a, **k: sent.append(t) or True) == "interested"
    assert len(sent) == 1 and sent[0].startswith("🟣 Réponse intéressée : ") and "Garage Dupont" in sent[0] and "(Troyes)" in sent[0] and "/local/7" in sent[0]


def test_alerte_en_echec_ne_bloque_pas_la_lecture(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("telegram HS")
    assert _apply_reply(monkeypatch, "Oui, rappelez-moi.", boom) == "interested"
