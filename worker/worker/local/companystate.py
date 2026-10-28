"""État OFFICIEL des entreprises à prospecter : fermées, cessées, radiées ou en procédure collective = jamais prospectées.

Constaté à la main (10/2026) : une entreprise en liquidation judiciaire (jugement du 17/03/2026) et un établissement fermé (transféré) étaient
encore dans la liste à prospecter. La découverte ne garde que des établissements actifs, mais l'état change ensuite ; et le contrôle « pas de
radiation, procédure collective ni fermeture dans le BODACC » de complete.py ne lisait que la colonne `bodacc`, posée seulement par la veille
des créations : aucune fiche existante n'était revérifiée.

Sources officielles GRATUITES, déjà utilisées par l'app, avec leur cadence (http.MIN_INTERVAL_S, cache 24 h en base) :
  * API Recherche d'entreprises (SIRENE, recherche-entreprises.api.gouv.fr, limite officielle 7 requêtes/s, ici ≤ 3/s) : état administratif
    de l'unité légale (A / C, date de fermeture) et de l'établissement (A / F, date de fermeture), une requête par fiche (par SIRET) ;
  * BODACC (annonces-commerciales, bodacc-datadila.opendatasoft.com, ≤ 2/s) : procédures collectives (liquidation, redressement, sauvegarde),
    radiations, dissolutions / cessations d'activité, une requête pour 20 SIREN.

Passage `company_state` de l'enrichissement (sans moteur, `enrich.free_steps`), borné (PER_RUN fiches, `deadline`) et idempotent (provenance
`company_state` datée) : fiches jamais vérifiées d'abord, non contactées avant les autres ; ensuite tous les RECHECK_DAYS jours, tous les
SEND_DAYS jours pour une fiche en file d'envoi ou en séquence de relances ; une erreur est reprise après ERROR_RETRY_HOURS. BODACC ou SIRENE
injoignable : rien n'est conclu (aucune fiche n'est dite « active » sans les deux réponses), le passage s'arrête.

Fermée (établissement fermé, unité légale cessée, liquidation / redressement / sauvegarde, radiation, dissolution) : `excluded_reason` clair
(« liquidation judiciaire (17/03/2026) », « établissement fermé (01/09/2026) »…), sortie de la file de rédaction (elle ne lit que les fiches
sans excluded_reason) et de la file d'envoi (auto_state 'queued' → NULL, brouillon conservé mais plus proposé ; séquence 'active' → 'stopped').
Transfert : SIRENE montre un AUTRE établissement actif de la même entreprise → l'ancien est exclu, l'autre est SIGNALÉ dans la provenance
(`active_elsewhere`), jamais créé ni deviné. L'envoi automatique la refuse (`send_check`, outbox) avant le premier message ET avant chaque relance.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time

from .. import db
from ..config import log
from . import bodacc, http, sirene, store

VERSION = 1


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


PER_RUN = _env_int("LOCAL_COMPANY_STATE_PER_RUN", 40)      # fiches revérifiées par passage (≈ 40 requêtes SIRENE + 2 BODACC, ~15 s)
RECHECK_DAYS = 30
SEND_DAYS = 7                       # fiche en file d'envoi ou en séquence : état de moins de 7 jours exigé (revérifié avant l'envoi sinon)
ERROR_RETRY_HOURS = 24
SEND_ERROR_RETRY = dt.timedelta(hours=1)    # envoi : source injoignable → pas de nouvel essai avant une heure (fiche passée, les autres partent)
LIQUIDATION_YEARS, RADIATION_YEARS, PROCEDURE_YEARS, END_YEARS = 5, 5, 3, 2
BODACC_YEARS = max(LIQUIDATION_YEARS, RADIATION_YEARS)

_LIFT = re.compile(r"extinction du passif|mettant fin [àa] la proc[ée]dure|infirm|r[ée]tractation|annulation|cl[ôo]ture de la proc[ée]dure de sauvegarde", re.I)
_LIQ = re.compile(r"liquidation|insuffisance d.actif", re.I)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fr(d: str | None) -> str:
    """« 2026-03-17 » → « 17/03/2026 » (vide si inconnue)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(d or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def _with_date(label: str, d: str | None) -> str:
    return f"{label} ({_fr(d)})" if _fr(d) else label


def _years_ago(today: dt.date, years: int) -> str:
    return (today - dt.timedelta(days=365 * years + years // 4)).isoformat()


def sirene_view(unit: dict | None, siret: str | None) -> dict | None:
    """Ce que dit SIRENE de l'unité légale et de NOTRE établissement (None : unité introuvable, rien n'est conclu par SIRENE)."""
    if unit is None:
        return None
    siret = re.sub(r"\D", "", siret or "")
    etabs = [e for e in [*(unit.get("matching_etablissements") or []), unit.get("siege") or {}] if isinstance(e, dict) and e.get("siret")]
    mine = next((e for e in etabs if str(e["siret"]) == siret), None)
    seen: set[str] = set()
    others = []
    for e in etabs:
        s = str(e["siret"])
        if s != siret and s not in seen and e.get("etat_administratif") == "A":
            seen.add(s)
            others.append({"siret": s, "address": str(e.get("adresse") or "")[:160] or None})
    return {"unit": unit.get("etat_administratif"), "unit_closed_at": unit.get("date_fermeture"),
            "etab": (mine or {}).get("etat_administratif"), "etab_closed_at": (mine or {}).get("date_fermeture"),
            "etab_found": mine is not None, "active_elsewhere": others[:3]}


def procedure(events: list[dict], today: dt.date) -> tuple[str, str] | None:
    """Procédure collective EN COURS d'après les jugements BODACC (du plus ancien au plus récent) : (libellé, date) ou None. Une liquidation
    (même clôturée pour insuffisance d'actif) compte LIQUIDATION_YEARS ans ; redressement / sauvegarde / autre PROCEDURE_YEARS ans ; un jugement
    qui y met fin (extinction du passif, infirmation, rétractation, fin de la sauvegarde) l'efface."""
    cur: tuple[str, str] | None = None
    for ev in events:
        if ev.get("kind") != "collective":
            continue
        n, d = str(ev.get("nature") or ""), str(ev.get("date") or ev.get("published") or "")[:10]
        if _LIFT.search(n):
            cur = None
        elif _LIQ.search(n):
            cur = ("liquidation judiciaire", d) if not cur or cur[0] != "liquidation judiciaire" else cur
        elif re.search(r"redressement", n, re.I):
            cur = cur if cur and cur[0] == "liquidation judiciaire" else ("redressement judiciaire", d)
        elif re.search(r"sauvegarde", n, re.I):
            cur = cur if cur and cur[0] != "procédure de sauvegarde" and cur[0] != "procédure collective" else ("procédure de sauvegarde", d)
        elif cur is None:
            cur = ("procédure collective", d)
    if cur is None:
        return None
    years = LIQUIDATION_YEARS if cur[0] == "liquidation judiciaire" else PROCEDURE_YEARS
    last = max((str(e.get("date") or e.get("published") or "")[:10] for e in events if e.get("kind") == "collective"), default=cur[1])
    return cur if last >= _years_ago(today, years) else None


def verdict(view: dict | None, events: list[dict], created_at, today: dt.date) -> dict:
    """{closed, reason, source} d'après SIRENE (`sirene_view`) et les événements BODACC (`bodacc.state_events`). Ordre : unité légale cessée,
    liquidation, radiation, redressement / sauvegarde, dissolution / cessation d'activité, établissement fermé."""
    created = str(created_at or "")[:10]
    found: list[tuple[str, str]] = []
    if view and view.get("unit") == "C":
        found.append((_with_date("entreprise cessée", view.get("unit_closed_at")), "sirene"))
    proc = procedure(events, today)
    if proc and proc[0] == "liquidation judiciaire":
        found.append((_with_date(*proc), "bodacc"))
    rads = [e for e in events if e.get("kind") == "radiation"]
    if rads:
        r = rads[-1]
        rd = str(r.get("date") or r.get("published") or "")[:10]
        later = [e for e in events if e.get("kind") == "creation" and str(e.get("published") or "") > str(r.get("published") or "")]
        if (not later and not re.search(r"transfert", str(r.get("nature") or ""), re.I) and rd >= _years_ago(today, RADIATION_YEARS)
                and (not created or rd >= created)):
            found.append((_with_date("radiée du RCS", rd), "bodacc"))
    if proc and proc[0] != "liquidation judiciaire":
        found.append((_with_date(*proc), "bodacc"))
    ends = [e for e in events if e.get("kind") == "company_end"]
    if ends:
        e = ends[-1]
        later = [x for x in events if x.get("kind") == "creation" and str(x.get("published") or "") > str(e.get("published") or "")]
        if not later and str(e.get("date") or "") >= _years_ago(today, END_YEARS) and (not created or str(e.get("date") or "") >= created):
            found.append((_with_date(str(e.get("nature") or "cessation d'activité"), e.get("date")), "bodacc"))
    if view and view.get("etab") == "F":
        label = _with_date("établissement fermé", view.get("etab_closed_at"))
        if view.get("unit") != "C" and view.get("active_elsewhere"):
            label += f" ; autre établissement actif de l'entreprise (SIRET {view['active_elsewhere'][0]['siret']}) : à vérifier, pas repris"
        found.append((label, "sirene"))
    if not found:
        return {"closed": False, "reason": None, "source": None}
    return {"closed": True, "reason": found[0][0][:250], "source": found[0][1]}


def check(conn, rows: list[dict], today: dt.date | None = None, events: dict[str, list[dict]] | None = None, at: str | None = None) -> dict[int, dict]:
    """État de chaque fiche {id: state}. BODACC d'abord, en lots (`events` : déjà lu par l'appelant), puis SIRENE fiche par fiche.
    http.ApiError (ou réseau) remonte : rien n'est conclu pour les fiches restantes."""
    today = today or dt.date.today()
    sirens = [r["siren"] for r in rows if re.fullmatch(r"\d{9}", str(r.get("siren") or ""))]
    if events is None:
        events = bodacc.state_events(sirens, since=dt.date.fromisoformat(_years_ago(today, BODACC_YEARS)), conn=conn)
    out: dict[int, dict] = {}
    for r in rows:
        view = sirene_view(sirene.lookup_establishment(r.get("siret"), r.get("siren"), conn), r.get("siret"))
        evs = events.get(str(r.get("siren") or ""), [])
        v = verdict(view, evs, r.get("company_created_at"), today)
        out[r["id"]] = {"checked_at": at or now_iso(), "version": VERSION, **v, "sirene": view if view is not None else "introuvable",
                        "bodacc": [{k: e.get(k) for k in ("kind", "nature", "date")} for e in evs if e.get("kind") != "creation"][-4:],
                        **({"active_elsewhere": view["active_elsewhere"]} if v["closed"] and view and view.get("etab") == "F"
                           and view.get("active_elsewhere") else {})}
    return out


def apply(conn, row: dict, state: dict) -> bool:
    """Provenance `company_state` posée ; fermée : écartée (excluded_reason), sortie de la file d'envoi (brouillon conservé), séquence de
    relances arrêtée. Une raison d'exclusion déjà posée n'est jamais remplacée. Renvoie : fermée ?"""
    store.set_provenance(conn, row["id"], "company_state", state)
    if not state.get("closed"):
        return False
    db.execute(conn, """UPDATE local_prospects SET excluded_reason=COALESCE(excluded_reason, %s),
                        auto_queued_at=IF(auto_state='queued', NULL, auto_queued_at),
                        auto_state=CASE auto_state WHEN 'queued' THEN NULL WHEN 'active' THEN 'stopped' ELSE auto_state END
                        WHERE id=%s""", (state["reason"], row["id"]))
    log.info("[local] fiche #%s écartée : %s", row["id"], state["reason"])
    return True


def _prov(path: str) -> str:
    return f"JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.company_state{('.' + path) if path else ''}')"


def _checked_before(interval: str) -> str:
    return (f"COALESCE(STR_TO_DATE(JSON_UNQUOTE({_prov('checked_at')}), '%%Y-%%m-%%dT%%H:%%i:%%sZ'), '2000-01-01') "
            f"< UTC_TIMESTAMP() - INTERVAL {interval}")


DUE = f"""p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND (p.category IS NULL OR p.category <> 'IGNORER')
        AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT') AND p.siren REGEXP '^[0-9]{{9}}$'
        AND (p.contacted_at IS NULL OR p.auto_state = 'active')
        AND ({_prov('')} IS NULL OR COALESCE(CAST(JSON_UNQUOTE({_prov('version')}) AS UNSIGNED), 0) < {VERSION}
             OR {_checked_before(f'{RECHECK_DAYS} DAY')}
             OR (p.auto_state IN ('queued','active') AND {_checked_before(f'{SEND_DAYS} DAY')})
             OR ({_prov('error')} IS NOT NULL AND {_checked_before(f'{ERROR_RETRY_HOURS} HOUR')}))"""
ORDER = f"""({_prov('')} IS NULL) DESC, (p.contacted_at IS NULL) DESC, (p.auto_state IN ('queued','active')) DESC,
        (p.draft_message IS NOT NULL) DESC, p.prospect_score DESC, p.id"""


def due(conn, limit: int) -> list[dict]:
    return db.fetch_all(conn, f"SELECT p.* FROM local_prospects p WHERE {DUE} ORDER BY {ORDER} LIMIT %s", (limit,))


def run(conn, limit: int = PER_RUN, deadline: float | None = None, today: dt.date | None = None) -> dict:
    """Un passage : au plus `limit` fiches dues (`DUE`). {done, found (fiches écartées), stop, detail}. Source injoignable : arrêt sans rien
    conclure pour les fiches restantes (stop « error »), reprises au passage suivant."""
    out = {"done": 0, "found": 0, "stop": "off", "detail": None}
    if limit <= 0:
        return out
    rows = due(conn, limit)
    out["stop"] = "limit" if len(rows) >= limit else "nothing"
    if not rows:
        return out
    today = today or dt.date.today()
    try:
        events = bodacc.state_events([r["siren"] for r in rows], since=dt.date.fromisoformat(_years_ago(today, BODACC_YEARS)), conn=conn)
    except Exception as exc:  # noqa: BLE001 — BODACC injoignable : aucune fiche n'est dite active sans lui
        conn.rollback()
        return {**out, "stop": "error", "detail": f"BODACC : {exc}"[:120]}
    for row in rows:
        if deadline is not None and time.monotonic() > deadline:
            out["stop"] = "time"
            break
        try:
            state = check(conn, [row], today, events)[row["id"]]
        except Exception as exc:  # noqa: BLE001 — SIRENE injoignable : rien de conclu, arrêt (les suivantes échoueraient de même)
            conn.rollback()
            out.update(stop="error", detail=f"SIRENE : {exc}"[:120])
            break
        out["found"] += int(apply(conn, row, state))
        out["done"] += 1
        conn.commit()
    return out


# ── Envoi automatique ───────────────────────────────────────────────────────────────────────────────
def _jv(v) -> dict:
    if isinstance(v, (str, bytes)):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _at(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def known_closed(p: dict) -> str | None:
    """Raison connue EN BASE de ne pas écrire à cette fiche (entreprise fermée / en procédure, ou écartée), sans requête ; None sinon."""
    from .complete import CLOSED_BODACC
    if p.get("excluded_reason"):
        return str(p["excluded_reason"])
    st = _jv(p.get("provenance")).get("company_state")
    if isinstance(st, dict) and st.get("closed"):
        return str(st.get("reason") or "entreprise fermée")
    if (_jv(p.get("bodacc")).get("kind") or "") in CLOSED_BODACC:
        return "radiation, procédure collective ou fermeture (BODACC)"
    return None


def send_retry_pending(p: dict, now: dt.datetime) -> bool:
    """La dernière revérification d'avant l'envoi a échoué (source injoignable) il y a moins de SEND_ERROR_RETRY : la fiche attend."""
    st = _jv(p.get("provenance")).get("company_state")
    at = _at(st.get("checked_at")) if isinstance(st, dict) and st.get("error") else None
    return at is not None and now - at < SEND_ERROR_RETRY


def send_check(conn, p: dict, now: dt.datetime) -> tuple[str, str]:
    """Juste avant un premier message ou une relance : ('ok', ''), ('closed', raison) — fiche écartée, sortie de la file, séquence arrêtée —
    ou ('retry', détail) : état de plus de SEND_DAYS jours et SIRENE / BODACC injoignable (rien d'envoyé, nouvel essai dans une heure).
    Fiche sans SIREN : rien à revérifier ('ok')."""
    if (why := known_closed(p)) is not None:
        return "closed", why
    st = _jv(p.get("provenance")).get("company_state")
    at = _at(st.get("checked_at")) if isinstance(st, dict) and not st.get("error") and int(st.get("version") or 0) >= VERSION else None
    if at is not None and now - at < dt.timedelta(days=SEND_DAYS):
        return "ok", ""
    if not re.fullmatch(r"\d{9}", str(p.get("siren") or "")):
        return "ok", ""
    try:
        state = check(conn, [p], now.date(), at=now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))[p["id"]]
    except Exception as exc:  # noqa: BLE001 — source injoignable : jamais d'envoi sur un état inconnu
        conn.rollback()
        prev = st if isinstance(st, dict) else {}
        store.set_provenance(conn, p["id"], "company_state", {**prev, "checked_at": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                              "error": f"{type(exc).__name__}: {exc}"[:160]})
        conn.commit()
        return "retry", "état officiel de l'entreprise pas vérifiable (SIRENE / BODACC injoignable)"
    closed = apply(conn, p, state)
    conn.commit()
    return ("closed", state["reason"]) if closed else ("ok", "")
