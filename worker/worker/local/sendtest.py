"""Un seul mail fictif au destinataire explicite, sans traiter ni modifier la file.

Usage : python -m worker.local.sendtest ADRESSE
"""
from __future__ import annotations

import argparse
import re
import sys
from email.headerregistry import Address

from .. import db
from . import maildns, mailer, outbox


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse réaffiche sinon les arguments surnuméraires (adresses, etc.).
        self.print_usage(sys.stderr)
        self.exit(2, "Arguments invalides : fournir exactement une adresse e-mail nue.\n")


def recipient(value: str) -> str:
    """Adresse nue uniquement : aucune liste, aucun nom ni en-tête injecté."""
    try:
        if not value.isascii() or re.search(r"[\s,;<>]", value):
            raise ValueError
        address = Address(addr_spec=value)
        if not address.username or "." not in address.domain or address.addr_spec != value:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Une seule adresse e-mail nue est requise.") from None
    return value


def sample_body(sender: dict) -> str:
    """Même enveloppe texte que web/lib/draft.ts:wrapDraft ; entreprise fictive."""
    name = sender["name"].split()[0]
    core = (
        f"Je m'appelle {name}, je suis développeur web indépendant près de Troyes et "
        "je m'intéresse à la présence sur internet de l'Atelier des Tilleuls.\n\n"
        "Pour votre entreprise de menuiserie, un site pourrait montrer vos meubles sur mesure "
        "et vos agencements. Il pourrait aussi faciliter les demandes de devis de vos futurs clients.\n\n"
        "Je m'occuperai de vous fournir un site entièrement sur mesure, du design aux textes "
        "jusqu'à la mise en ligne et la maintenance.\n\n"
        "Est-ce que ce serait un projet qui vous intéresserait ?\n\n"
        "Je vous partage mon site pour plus d'informations."
    )
    signature = "\n".join(sender[k] for k in ("name", "company", "phone", "email", "website") if sender.get(k))
    notice = (
        "Coordonnées trouvées sur des sources publiques (annuaire officiel des entreprises, site internet). "
        "Si vous ne souhaitez plus de message de ma part, dites-le-moi simplement. "
        f"En savoir plus : {sender['privacy_url'].strip()}"
    )
    return f"Bonjour,\n\n{core}\n\n{mailer.CLOSING}\n{signature}\n\n--\n{notice}"


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("adresse", type=recipient, help="destinataire unique du mail de test")
    args = parser.parse_args(argv)
    try:
        cfg = outbox.mail_config()
        if outbox.missing_config(cfg):
            print("Configuration SMTP incomplète. Aucun envoi.", file=sys.stderr)
            return 1
        # Même contrôle bloquant que l'outbox, sans cache ni écriture de son état.
        dns = maildns.check(mailer.email_domain(cfg["from"]), cfg["dkim_selector"])
        if not dns["ok"]:
            categories = [key for key in ("SPF", "DMARC", "DKIM") if any(key in p for p in dns["problems"])]
            reason = ", ".join(categories) + " absent ou multiple" if categories else "vérification indisponible"
            print(f"Contrôle DNS refusé : {reason}. Aucun envoi SMTP.", file=sys.stderr)
            return 1
        if dns["warnings"]:
            print("Avertissement DNS : contrôle accepté avec réserves (DKIM non vérifié ou DMARC p=none).", file=sys.stderr)
        with db.connect() as conn:
            sender = outbox._setting(conn, "local_sender")
        if not all(isinstance(sender.get(k), str) and sender[k].strip() for k in ("name", "privacy_url")):
            print("Signature incomplète : nom et notice requis dans Réglages. Aucun envoi.", file=sys.stderr)
            return 1
        msg = mailer.build_message(
            from_addr=cfg["from"], from_name=cfg["from_name"] or sender["name"],
            to=args.adresse, subject="Atelier des Tilleuls : un site pour votre menuiserie",
            text=sample_body(sender), sender=sender,
        )
        outbox.smtp_send(msg, cfg)
    except Exception:
        # Une erreur SMTP/DB peut contenir adresses, identifiants ou réponse du serveur.
        # Pas de nouvelle tentative : une erreur après DATA peut suivre une acceptation.
        print("Test interrompu ; aucune nouvelle tentative. Vérifier le service concerné avant de relancer.", file=sys.stderr)
        return 1
    print("Un mail de test accepté par le serveur SMTP (entreprise fictive).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
