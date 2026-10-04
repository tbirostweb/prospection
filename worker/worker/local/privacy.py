"""Droits des personnes (RGPD art. 15, 17, 20, 21) : export et effacement des données d'une entreprise / d'un contact.

Réservé à l'exploitant (accès au conteneur worker) : aucune route web, donc aucune exposition publique. Vérifier
l'identité du demandeur de façon proportionnée AVANT d'exécuter (ex. réponse depuis l'adresse concernée).

    python -m worker.local.privacy export --siret 12345678900011         (JSON sur la sortie standard)
    python -m worker.local.privacy export --email contact@exemple.fr
    python -m worker.local.privacy erase  --siret 12345678900011          (effacement + opposition conservée)
    python -m worker.local.privacy erase  --email contact@exemple.fr --no-opposition

Effacement : prospects, retours de site, sites signalés faux et résultats d'apprentissage liés sont supprimés. Par défaut une
entrée MINIMALE est gardée (ou créée) dans `local_do_not_contact` pour que l'entreprise ne soit jamais recontactée ni
re-découverte ; `--no-opposition` la supprime aussi (effacement total, la personne pourra alors être re-découverte).
Les sauvegardes suivent leur propre durée de rotation : documenter la date d'effacement effectif dans la réponse.
"""
from __future__ import annotations

import argparse
import json
import sys

from .. import db


def _where(siret: str | None, email: str | None) -> tuple[str, tuple]:
    if not siret and not email:
        raise ValueError("--siret ou --email obligatoire")
    clauses, params = [], []
    if siret:
        clauses.append("siret = %s")
        params.append(siret)
    if email:
        clauses.append("LOWER(email) = LOWER(%s)")
        params.append(email)
    return "(" + " OR ".join(clauses) + ")", tuple(params)


def export(conn, siret: str | None = None, email: str | None = None) -> dict:
    where, params = _where(siret, email)
    prospects = db.fetch_all(conn, f"SELECT * FROM local_prospects WHERE {where}", params)
    sirets = sorted({p["siret"] for p in prospects if p.get("siret")} | ({siret} if siret else set()))
    ids = [p["id"] for p in prospects]
    out = {"prospects": prospects, "do_not_contact": db.fetch_all(conn, f"SELECT * FROM local_do_not_contact WHERE {where}", params)}
    keys = sirets + [f"id:{i}" for i in ids]
    out["outcomes"] = db.fetch_all(conn, f"SELECT * FROM local_outcomes WHERE okey IN ({','.join(['%s'] * len(keys))})", tuple(keys)) if keys else []
    if sirets or ids:
        cond = " OR ".join(filter(None, [f"siret IN ({','.join(['%s'] * len(sirets))})" if sirets else "",
                                         f"prospect_id IN ({','.join(['%s'] * len(ids))})" if ids else ""]))
        out["site_feedback"] = db.fetch_all(conn, f"SELECT * FROM local_site_feedback WHERE {cond}", tuple(sirets) + tuple(ids))
    else:
        out["site_feedback"] = []
    out["bad_sites"] = db.fetch_all(conn, f"SELECT * FROM local_bad_sites WHERE siret IN ({','.join(['%s'] * len(sirets))})", tuple(sirets)) if sirets else []
    return out


def erase(conn, siret: str | None = None, email: str | None = None, keep_opposition: bool = True) -> dict:
    data = export(conn, siret, email)
    sirets = sorted({p["siret"] for p in data["prospects"] if p.get("siret")} | ({siret} if siret else set()))
    ids = [p["id"] for p in data["prospects"]]
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        if keep_opposition:
            first = data["prospects"][0] if data["prospects"] else {}
            if not data["do_not_contact"]:
                cur.execute("INSERT INTO local_do_not_contact (siret, siren, email, reason) VALUES (%s,%s,%s,%s)",
                            (siret or first.get("siret"), first.get("siren"), email or first.get("email"), "demande d'effacement (RGPD)"))
        else:
            where, params = _where(siret, email)
            cur.execute(f"DELETE FROM local_do_not_contact WHERE {where}", params)
            counts["do_not_contact"] = cur.rowcount
        for o in data["outcomes"]:
            cur.execute("DELETE FROM local_outcomes WHERE okey=%s", (o["okey"],))
        counts["outcomes"] = len(data["outcomes"])
        for f in data["site_feedback"]:
            cur.execute("DELETE FROM local_site_feedback WHERE id=%s", (f["id"],))
        counts["site_feedback"] = len(data["site_feedback"])
        if sirets:
            cur.execute(f"DELETE FROM local_bad_sites WHERE siret IN ({','.join(['%s'] * len(sirets))})", tuple(sirets))
            counts["bad_sites"] = cur.rowcount
        if ids:
            cur.execute(f"DELETE FROM local_prospects WHERE id IN ({','.join(['%s'] * len(ids))})", tuple(ids))
        counts["prospects"] = len(ids)
    conn.commit()
    return counts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m worker.local.privacy")
    ap.add_argument("action", choices=("export", "erase"))
    ap.add_argument("--siret")
    ap.add_argument("--email")
    ap.add_argument("--no-opposition", action="store_true", help="effacement total, y compris la liste « ne plus contacter »")
    a = ap.parse_args(argv)
    if not a.siret and not a.email:
        ap.error("--siret ou --email obligatoire")
    with db.connect() as conn:
        if a.action == "export":
            json.dump(export(conn, a.siret, a.email), sys.stdout, ensure_ascii=False, indent=2, default=str)
            print()
        else:
            print(json.dumps(erase(conn, a.siret, a.email, keep_opposition=not a.no_opposition)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
