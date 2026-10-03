"""APPRENTISSAGE à partir de TES résultats : quels métiers, quelles villes, quels signaux et quels messages obtiennent des réponses ?

Chaque prospect que tu as jugé ou contacté devient un exemple :
  réussite   🏆 client obtenu (1) · intéressé (0,8) · réponse reçue (0,5)
  échec      perdu / pas intéressé / 🚫 pas adapté / « sans réponse » / contacté il y a plus de 21 jours sans suite (0)
  ignoré     « mauvais site » et « chaîne » (erreurs de données, pas un verdict commercial), prospects jamais jugés
Pour chaque caractéristique (métier, ville, signal d'achat, état du site), on compare son taux de réussite au taux moyen, LISSÉ (quelques
exemples ne suffisent pas à conclure : un métier à 1/1 n'est pas « 100 % »). L'écart donne au plus ±5 points par caractéristique et ±10 au
total, affichés dans le détail du score : rien n'est caché, rien ne remplace le barème. Tant qu'il y a moins de 10 résultats et 3 réussites,
l'apprentissage ne modifie aucun score. Les résultats sont archivés dans `local_outcomes`, qui SURVIT au bouton « Tout effacer ». Les messages (type de brouillon) sont mesurés à part : ils ne changent pas le score d'un prospect.

  python -m worker.local.learning      recalcule et affiche ce que l'application a appris
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

from .. import db
from .textmatch import fold

SETTING_KEY = "local_learning"
MIN_OUTCOMES = 10          # résultats au total avant tout ajustement
MIN_SUCCESSES = 3
MIN_N = 4                  # exemples minimum pour qu'une caractéristique compte
PSEUDO = 4                 # lissage : chaque caractéristique part de 4 exemples « moyens »
PER_FEATURE_MAX = 5.0
TOTAL_MAX = 10.0
NO_REPLY_DAYS = 21
IGNORED_REASONS = ("wrong_site", "chain")
KIND_LABELS = {"metier": "métier", "ville": "ville", "signal": "signal", "site": "site"}
SITE_LABELS = {"CONFIRMED": "site confirmé", "PROBABLE": "site probable", "UNCERTAIN": "site incertain", "NOT_FOUND": "sans site trouvé", "UNREACHABLE": "site en panne"}


def outcome(r: dict, now: datetime | None = None) -> float | None:
    """1 / 0,8 / 0,5 (réussites) · 0 (échec) · None (pas un résultat exploitable)."""
    if (r.get("feedback_reason") or "") in IGNORED_REASONS:
        return None
    st, rs = r.get("status"), r.get("response_status")
    if st == "WON" or rs == "WON":
        return 1.0
    if st == "INTERESTED" or rs == "INTERESTED":
        return 0.8
    if st == "REPLIED" or rs == "REPLIED":
        return 0.5
    if st == "LOST" or rs in ("NOT_INTERESTED", "NO_ANSWER", "NOT_A_FIT"):
        return 0.0
    if st == "CONTACTED":
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        at = r.get("last_contacted_at") or r.get("contacted_at")
        if at is not None and at < now - timedelta(days=NO_REPLY_DAYS):
            return 0.0
    return None


def features(r: dict) -> list[str]:
    """Caractéristiques d'un prospect, sous forme « type:valeur »."""
    out = []
    if r.get("activity_key"):
        out.append(f"metier:{r['activity_key']}")
    if r.get("city"):
        out.append(f"ville:{fold(r['city']).upper()}")
    if r.get("website_status"):
        out.append(f"site:{r['website_status']}")
    sigs = r.get("buy_signals")
    if isinstance(sigs, (str, bytes)):
        sigs = json.loads(sigs)
    out += [f"signal:{s['code']}" for s in sigs or [] if isinstance(s, dict) and s.get("code")]
    return out


def compute(rows: list[dict], now: datetime | None = None) -> dict:
    """Modèle : {'outcomes','successes','prior','active','features': {feat: {n, score, rate, points, label}}, 'messages': {...}}."""
    samples = [(float(o), r) for r in rows if (o := (r["outcome"] if "outcome" in r else outcome(r, now))) is not None]
    n_total = len(samples)
    succ = sum(1 for o, _ in samples if o > 0)
    total = sum(o for o, _ in samples)
    prior = (total + 0.5) / (n_total + 2) if n_total else 0.0
    agg: dict[str, list[float]] = {}
    for o, r in samples:
        for f in features(r):
            agg.setdefault(f, []).append(o)
    feats = {}
    for f, os_ in agg.items():
        n, s = len(os_), sum(os_)
        rate = (s + PSEUDO * prior) / (n + PSEUDO) if n + PSEUDO else prior
        pts = 0.0
        if n >= MIN_N and prior > 0:
            pts = max(-PER_FEATURE_MAX, min(PER_FEATURE_MAX, round((rate / prior - 1) * 6, 1)))
        kind, _, val = f.partition(":")
        label = SITE_LABELS.get(val, val) if kind == "site" else val.lower() if kind == "ville" else val
        feats[f] = {"n": n, "score": round(s, 2), "wins": sum(1 for x in os_ if x > 0), "rate": round(rate, 3), "points": pts,
                    "label": f"{KIND_LABELS.get(kind, kind)} « {label} »"}
    messages: dict[str, dict] = {}
    for o, r in samples:
        if r.get("draft_kind") and r.get("contacted_at") is not None:
            m = messages.setdefault(r["draft_kind"], {"n": 0, "wins": 0})
            m["n"] += 1
            m["wins"] += int(o > 0)
    active = n_total >= MIN_OUTCOMES and succ >= MIN_SUCCESSES
    return {"outcomes": n_total, "successes": succ, "prior": round(prior, 3), "active": active, "features": feats, "messages": messages,
            "min_outcomes": MIN_OUTCOMES, "min_successes": MIN_SUCCESSES}


def adjustment(model: dict | None, p: dict, signal_codes: list[str]) -> tuple[float, str]:
    """(points -10..+10, explication) pour le prospect `p` d'après le modèle. (0, raison) tant que l'apprentissage n'est pas actif."""
    if not model:
        return 0.0, "pas encore de résultats enregistrés"
    if not model.get("active"):
        return 0.0, (f"pas encore assez de résultats : {model.get('outcomes', 0)}/{MIN_OUTCOMES} jugés, "
                     f"{model.get('successes', 0)}/{MIN_SUCCESSES} réussites — marque tes prospects contactés (réponse, client, perdu, sans réponse)")
    feats = model.get("features") or {}
    keys = features({**p, "buy_signals": [{"code": c} for c in signal_codes]})
    used = [(feats[k]) for k in keys if k in feats and feats[k]["points"]]
    if not used:
        return 0.0, f"rien de marquant dans tes {model['outcomes']} résultats pour ce type de prospect"
    pts = max(-TOTAL_MAX, min(TOTAL_MAX, round(sum(f["points"] for f in used), 1)))
    detail = " · ".join(f"{f['label']} {f['wins']}/{f['n']} ({'+' if f['points'] > 0 else ''}{f['points']:g})" for f in sorted(used, key=lambda f: -abs(f["points"]))[:4])
    return pts, f"d'après tes {model['outcomes']} résultats (moyenne {round(100 * model['prior'])} %) : {detail}"


SQL = """SELECT id, siret, activity_key, city, website_status, buy_signals, status, response_status, feedback_reason, contacted_at, last_contacted_at, draft_kind
         FROM local_prospects WHERE status IN ('CONTACTED','REPLIED','INTERESTED','WON','LOST') OR response_status IS NOT NULL"""


def fingerprint(model: dict) -> str:
    core = {k: v for k, v in model.items() if k not in ("at", "fingerprint")}
    return hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]


def archive(conn) -> int:
    """Copie les résultats des prospects actuels dans `local_outcomes` (conservée par la remise à zéro). Renvoie le nombre archivé."""
    n = 0
    for r in db.fetch_all(conn, SQL):
        key = r.get("siret") or f"id:{r['id']}"
        o = outcome(r)
        if o is None:
            db.execute(conn, "DELETE FROM local_outcomes WHERE okey=%s", (key,))
            continue
        sigs = r.get("buy_signals")
        sigs = json.loads(sigs) if isinstance(sigs, (str, bytes)) else sigs
        db.execute(conn, """INSERT INTO local_outcomes (okey, activity_key, city, website_status, buy_signals, draft_kind, contacted_at, outcome)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE activity_key=VALUES(activity_key), city=VALUES(city), website_status=VALUES(website_status),
                              buy_signals=VALUES(buy_signals), draft_kind=VALUES(draft_kind), contacted_at=VALUES(contacted_at), outcome=VALUES(outcome)""",
                   (key, r.get("activity_key"), r.get("city"), r.get("website_status"), json.dumps([{"code": x.get("code")} for x in sigs or [] if isinstance(x, dict)]),
                    r.get("draft_kind"), r.get("contacted_at"), o))
        n += 1
    return n


def effect(model: dict | None) -> str:
    """Ce qui change réellement les scores : rien tant que l'apprentissage est inactif, sinon les points de chaque caractéristique."""
    if not model or not model.get("active"):
        return "inactive"
    return fingerprint({f: v["points"] for f, v in (model.get("features") or {}).items() if v.get("points")})


def refresh(conn) -> tuple[dict, bool]:
    """Archive les résultats, recalcule le modèle et l'enregistre (`settings.local_learning`).
    Renvoie (modèle, faut-il rescorer ?) — vrai seulement si les ajustements de score ont changé."""
    archive(conn)
    conn.commit()
    model = compute(db.fetch_all(conn, "SELECT activity_key, city, website_status, buy_signals, draft_kind, contacted_at, outcome FROM local_outcomes"))
    model["fingerprint"] = fingerprint(model)
    model["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey=%s LIMIT 1", (SETTING_KEY,))
    old = json.loads(row["value_json"]) if row and isinstance(row["value_json"], (str, bytes)) else (row or {}).get("value_json")
    changed = False
    if (old or {}).get("fingerprint") != model["fingerprint"]:
        user = db.fetch_one(conn, "SELECT id FROM users ORDER BY id LIMIT 1")
        changed = user is not None and effect(old) != effect(model)     # sans utilisateur, rien n'est enregistré : rien à rescorer
        if user:
            db.execute(conn, """INSERT INTO settings (user_id, skey, value_json) VALUES (%s,%s,%s)
                                ON DUPLICATE KEY UPDATE value_json=VALUES(value_json)""", (user["id"], SETTING_KEY, json.dumps(model, ensure_ascii=False)))
            conn.commit()
    return model, changed


def main() -> int:
    with db.connect() as conn:
        model, _ = refresh(conn)
    print(f"{model['outcomes']} résultat(s), {model['successes']} réussite(s), moyenne {round(100 * model['prior'])} % — "
          + ("apprentissage ACTIF" if model["active"] else f"inactif (il faut {MIN_OUTCOMES} résultats dont {MIN_SUCCESSES} réussites)"))
    for f, v in sorted(model["features"].items(), key=lambda kv: -kv[1]["points"]):
        if v["n"] >= MIN_N:
            print(f"  {v['points']:+5.1f}  {v['label']:<40} {v['wins']}/{v['n']}")
    for k, m in model["messages"].items():
        print(f"  message « {k} » : {m['wins']}/{m['n']} réponse(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
