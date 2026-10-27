"""Rattrapage des métiers « Non classée » d'après le code NAF, sur un VRAI MySQL : idempotent, fiches déjà classées intactes."""
from __future__ import annotations

from worker import db
from worker.local import naf, naflabels

from .test_local_pipeline import add_campaign


def _add(conn, siret, naf_code, label, key=None, cid=None):
    pid = db.execute(conn, """INSERT INTO local_prospects (siret, siren, fingerprint, company_name, city, naf_code, activity_label, activity_key, status)
                              VALUES (%s,%s,%s,%s,'TROYES',%s,%s,%s,'DISCOVERED')""", (siret, siret[:9], siret, f"ENTREPRISE {siret}", naf_code, label, key))
    if cid:
        db.execute(conn, "INSERT INTO local_prospect_campaigns (prospect_id, campaign_id) VALUES (%s,%s)", (pid, cid))
    conn.commit()
    return pid


def _row(conn, pid):
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))


def test_rattrapage_idempotent_et_fiches_classees_intactes(env, conn):
    cid = add_campaign(conn)["id"]
    a = _add(conn, "30000000000011", "43.29A", None, cid=cid)                  # hors catalogue : intitulé officiel
    b = _add(conn, "30000000000012", "4322A", "Non classée", cid=cid)           # code sans point, présent au catalogue
    c = _add(conn, "30000000000013", "43.99X", "", cid=cid)                     # sous-classe inconnue : division
    d = _add(conn, "30000000000014", "01.11Z", None, cid=cid)                   # division hors table : reste inconnu
    e = _add(conn, "30000000000015", "56.10A", "Crêperie du coin", "restaurants", cid=cid)   # déjà classée : jamais touchée
    f = _add(conn, "30000000000016", None, None, cid=cid)                       # sans code NAF : ignorée
    stats = naflabels.backfill(conn)
    assert stats == {"examined": 4, "labelled": 3, "unknown": 1}
    assert _row(conn, a)["activity_label"] == "Isolation" and _row(conn, a)["activity_key"] is None
    assert _row(conn, b)["activity_label"] == naf.CATALOG["plombiers"]["label"] and _row(conn, b)["activity_key"] == "plombiers"
    assert _row(conn, c)["activity_label"] == "Travaux de construction spécialisés"
    assert _row(conn, d)["activity_label"] is None
    assert _row(conn, e)["activity_label"] == "Crêperie du coin" and _row(conn, f)["activity_label"] is None
    assert '"naf_rev2"' in _row(conn, a)["provenance"] and _row(conn, a)["scored_at"] is not None
    again = naflabels.backfill(conn)                                          # rien à refaire : ni la fiche inconnue (même version de table)
    assert again == {"examined": 0, "labelled": 0, "unknown": 0}


def test_une_nouvelle_version_de_la_table_reexamine_les_inconnues(env, conn, monkeypatch):
    cid = add_campaign(conn)["id"]
    d = _add(conn, "30000000000021", "01.11Z", None, cid=cid)
    assert naflabels.backfill(conn)["unknown"] == 1
    table = naf.official()
    monkeypatch.setattr(naf, "_OFFICIAL", {**table, "version": "v-suivante", "divisions": {**table["divisions"], "01": {"intitule": "Culture", "court": "Cultures"}}})
    assert naflabels.backfill(conn)["labelled"] == 1 and _row(conn, d)["activity_label"] == "Cultures"
