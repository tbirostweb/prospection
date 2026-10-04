"""Conservation limitée des prospects travaillés + droits des personnes (export / effacement), sur un vrai MySQL. Données synthétiques."""
from __future__ import annotations

from worker import db
from worker.local import privacy, retention


def _add(conn, siret, months_ago, **over):
    row = {"siret": siret, "fingerprint": siret, "company_name": siret, **over}
    pid = db.execute(conn, f"INSERT INTO local_prospects ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
    db.execute(conn, "UPDATE local_prospects SET discovered_at = UTC_TIMESTAMP() - INTERVAL %s MONTH WHERE id=%s", (months_ago, pid))
    return pid


def test_prospects_travailles_purges_apres_36_mois_sauf_gagnes(env, conn):
    old = "UTC_TIMESTAMP() - INTERVAL 40 MONTH"
    a = _add(conn, "w_old", 48, status="LOST", response_status="NOT_INTERESTED")
    db.execute(conn, f"UPDATE local_prospects SET contacted_at={old}, last_contacted_at={old} WHERE id=%s", (a,))
    b = _add(conn, "w_recent", 48, status="CONTACTED")
    db.execute(conn, "UPDATE local_prospects SET contacted_at=UTC_TIMESTAMP() - INTERVAL 40 MONTH, last_contacted_at=UTC_TIMESTAMP() - INTERVAL 2 MONTH WHERE id=%s", (b,))
    _add(conn, "w_won", 60, status="WON", contacted_at="2021-01-01")
    _add(conn, "w_note", 40, status="TO_CONTACT", notes="ancienne note")
    _add(conn, "dnc_old", 40, do_not_contact=1, status="DO_NOT_CONTACT")
    db.execute(conn, "INSERT INTO local_do_not_contact (siret, reason) VALUES ('dnc_old','a demandé')")
    db.execute(conn, "INSERT INTO local_site_feedback (prospect_id, siret, kind, url, company, created_at) VALUES (%s,'w_old','wrong_site','https://x.test/','{}', UTC_TIMESTAMP() - INTERVAL 40 MONTH)", (a,))
    db.execute(conn, "INSERT INTO local_site_feedback (prospect_id, siret, kind, url, company, created_at) VALUES (%s,'w_recent','wrong_site','https://y.test/','{}', UTC_TIMESTAMP() - INTERVAL 40 MONTH)", (b,))
    conn.commit()
    out = retention.purge(conn)
    left = {r["siret"] for r in db.fetch_all(conn, "SELECT siret FROM local_prospects")}
    assert left == {"w_recent", "w_won"}
    assert out["prospects_worked"] == 3
    # l'opposition reste respectée : entrée minimale conservée
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_do_not_contact WHERE siret='dnc_old'")["n"] == 1
    # résultat du prospect purgé : archivé PUIS anonymisé (plus de SIRET) ; celui du prospect conservé garde sa clé
    keys = {r["okey"] for r in db.fetch_all(conn, "SELECT okey FROM local_outcomes")}
    assert "w_old" not in keys and any(k.startswith("a:") for k in keys)
    # retour de site du prospect purgé supprimé, celui du prospect conservé gardé
    assert {r["siret"] for r in db.fetch_all(conn, "SELECT siret FROM local_site_feedback")} == {"w_recent"}
    # idempotent
    again = retention.purge(conn)
    assert again["prospects_worked"] == 0 and again["outcomes_anonymized"] == 0


def test_export_puis_effacement_avec_opposition_conservee(env, conn):
    pid = _add(conn, "00000000000026", 1, email="contact@synthetique.test", status="CONTACTED", contacted_at="2026-01-01")
    db.execute(conn, "INSERT INTO local_site_feedback (prospect_id, siret, kind, url, company) VALUES (%s,'00000000000026','found_site','https://z.test/','{}')", (pid,))
    db.execute(conn, "INSERT INTO local_bad_sites (siret, fingerprint, domain, reason) VALUES ('00000000000026','f','bad.test','x')")
    conn.commit()
    from worker.local import learning
    learning.archive(conn)
    conn.commit()
    data = privacy.export(conn, email="CONTACT@synthetique.test")
    assert len(data["prospects"]) == 1 and len(data["site_feedback"]) == 1 and len(data["bad_sites"]) == 1 and len(data["outcomes"]) == 1
    counts = privacy.erase(conn, siret="00000000000026")
    assert counts["prospects"] == 1 and counts["outcomes"] == 1 and counts["site_feedback"] == 1
    after = privacy.export(conn, siret="00000000000026")
    assert not after["prospects"] and not after["outcomes"] and not after["site_feedback"] and not after["bad_sites"]
    assert len(after["do_not_contact"]) == 1                                         # opposition minimale conservée
    privacy.erase(conn, siret="00000000000026", keep_opposition=False)
    assert not privacy.export(conn, siret="00000000000026")["do_not_contact"]


def test_effacement_sans_critere_refuse(env, conn):
    import pytest
    with pytest.raises(ValueError):
        privacy.export(conn)
