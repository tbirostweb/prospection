"""Score commercial d'un prospect local (0-100) — EXPLICABLE point par point, jamais produit par Ollama.

    Potentiel site           30   aucun site trouvé 30 · injoignable 25 · à moderniser jusqu'à 20 · site déjà très bon 0-5 · incertain / non vérifié ≈ 10
    Compatibilité business   20   dépendance de l'activité au web ; chaîne, franchise ou grosse entreprise fortement pénalisées
    Opportunité SEO/technique 20  bases SEO manquantes, performance ; « présence digitale faible » quand aucun site n'est trouvé
    Localisation             10   proche = mieux, jamais obligatoire
    Fraîcheur entreprise      5   création récente (SIRENE, confirmée par BODACC = petit bonus)
    Contactabilité           10   contact professionnel public trouvé sur le site officiel
    Fiabilité des données     5   entreprise (SIRET) et site correctement identifiés
Puis trois AJUSTEMENTS expliqués, ajoutés au score brut (avant les plafonds) :
    Signaux d'achat       +0 à +12  reprise, ouverture récente sans site, changement de nom, déménagement, site en panne… (signals.py)
    Budget probable       -8 à +8   valeur d'un client dans le métier, chiffre d'affaires publié, effectif (budget.py)
    Tes résultats         -10 à +10 ce qui a marché ou non dans TES contacts : métier, ville, signal, état du site (learning.py)
DEUX scores séparés puis un score final : `commercialPotentialScore` (l'intérêt commercial apparent : site, activité, SEO, proximité, fraîcheur) et
`dataConfidenceScore` (la QUALITÉ des données : identité, site, audit, contact, chaîne). Le score final ne dépasse jamais certaines limites quand
les données sont faibles, et chaque plafond est expliqué (score brut → plafond → raison). « Très bon » exige des données fiables, un besoin
potentiel OBJECTIVEMENT observable, un contact exploitable et une activité pertinente : « site non trouvé » ne suffit jamais.
Un score Lighthouse faible est un SIGNAL parmi d'autres, jamais une raison suffisante. Tous les poids et seuils sont réglables (`settings.local_weights`
/ `local_thresholds`). « Site non trouvé » n'est pas « pas de site » : la formulation et la confiance en tiennent compte.
"""
from __future__ import annotations

from datetime import date

from . import budget as budget_mod, learning as learning_mod, naf as naf_mod, signals as signals_mod

DEFAULT_WEIGHTS = {"site_potential": 30, "business_fit": 20, "seo_technical": 20, "proximity": 10, "freshness": 5,
                   "contactability": 10, "data_reliability": 5}
LABELS = {"site_potential": "Potentiel site", "business_fit": "Compatibilité business", "seo_technical": "Opportunité SEO / technique",
          "proximity": "Localisation", "freshness": "Fraîcheur de l'entreprise", "contactability": "Contactabilité", "data_reliability": "Fiabilité des données"}
DEFAULT_THRESHOLDS = {"TRES_BON": 80, "A_CONTACTER": 62, "A_EXAMINER": 45, "FAIBLE": 30}
CATEGORY_LABELS = {"TRES_BON": "🔥 Très bon prospect", "A_CONTACTER": "🟢 À contacter", "A_EXAMINER": "🟡 À examiner",
                   "FAIBLE": "⚪ Faible priorité", "IGNORER": "🚫 Ignorer"}
CHAIN_CAP = 0              # chaîne, réseau ou franchise : site fourni par l'enseigne, rien à leur vendre
PRELIMINARY_CAP = 60
ADJUSTMENTS = ("buy_signals", "budget", "learning")
# PRIORITÉ DE CIBLAGE (réglage `local_options.focus`) : part du « potentiel site » selon la situation. « redesign » (défaut) = d'abord les
# entreprises qui ONT un site daté ou faible (refonte : le besoin se voit, se montre et se chiffre) ; « no_site » = d'abord celles sans site.
DEFAULT_FOCUS = "redesign"   # celui de l'application (runner.Ctx) ; la fonction garde « no_site » par défaut (barème historique, tests)
FOCUS = {  # (site à moderniser HIGH, MEDIUM, LOW, aucun site trouvé)
    "redesign": (1.0, 0.7, 0.13, 0.5),
    "balanced": (0.85, 0.55, 0.13, 0.85),
    "no_site": (20 / 30, 0.4, 0.13, 1.0),
}
NOISY_CAP = 29             # activité bruitée (restaurants, bars, boulangeries, commerces…) SANS vrai signal : écartée du ciblage
NO_SITE_CAP = 74           # « site non trouvé » n'est jamais une certitude : jamais « Très bon » sur ce seul signal
NO_CONTACT_CAP = 70        # aucun moyen de contact public trouvé : on ne peut pas agir
DATA_CONF_MIN_TRES_BON = 70
DATA_CONF_MIN_CONTACT = 50
DATA_CONF_FLOOR = 40       # en dessous : au mieux « À examiner »
SITE_OK = ("CONFIRMED", "PROBABLE")
SMALL_EMPLOYEES = {None, "NN", "00", "01", "02", "03"}


def merge_weights(custom: dict | None) -> dict:
    """Poids enregistrés + défauts. Un réglage incomplet ou incohérent (clés inconnues) ne casse rien."""
    w = {k: float(v) for k, v in (custom or {}).items() if k in DEFAULT_WEIGHTS and isinstance(v, (int, float))}
    return {**DEFAULT_WEIGHTS, **w}


def proximity_factor(distance_km: float | None) -> float:
    if distance_km is None:
        return 0.4                                           # inconnue : neutre, jamais bonifiée
    return 1.0 if distance_km <= 2 else 0.85 if distance_km <= 5 else 0.65 if distance_km <= 10 else 0.4 if distance_km <= 20 else 0.25 if distance_km <= 30 else 0.1


def freshness_factor(created: date | None, bodacc: dict | None, today: date) -> tuple[float, str]:
    months = None if created is None else (today.year - created.year) * 12 + today.month - created.month
    f = 0.0 if months is None else 1.0 if months <= 6 else 0.7 if months <= 12 else 0.4 if months <= 24 else 0.2 if months <= 36 else 0.0
    note = "date de création inconnue" if months is None else f"créée il y a {months} mois" if months >= 0 else "création à venir"
    if bodacc and bodacc.get("kind") == "creation":
        f = min(1.0, f + 0.2)
        note += " · création confirmée au BODACC"
    return f, note


def has_contact(p: dict) -> bool:
    """Un moyen de contact professionnel public ou un canal officiel clair (formulaire / page contact)."""
    return bool(p.get("phone") or p.get("email") or p.get("contact_form") or p.get("contact_page"))


def contact_factor(p: dict) -> tuple[float, str]:
    conf = p.get("contact_confidence")
    mult = 1.0 if conf is None else 0.6 + 0.4 * float(conf)
    if p.get("email"):
        kind = p.get("email_kind")
        f, note = {"GENERIC_BUSINESS": (1.0, "adresse générique professionnelle"), "PERSONAL_BUSINESS": (0.8, "adresse nominative professionnelle (domaine de l'entreprise)"),
                   "UNCERTAIN": (0.55, "adresse publiée sur son site (catégorie incertaine : messagerie grand public ou forme ambiguë)")}.get(
            kind, (0.55, "adresse publiée sur son site"))
        return f * mult, note
    if p.get("phone"):
        return 0.5 * mult, "téléphone professionnel publié"
    if p.get("contact_form"):
        return 0.45 * mult, "formulaire de contact officiel"
    if p.get("contact_page"):
        return 0.4 * mult, "page contact officielle"
    return 0.0, "aucune coordonnée professionnelle trouvée (≠ l'entreprise n'en a pas)"


def data_confidence(p: dict) -> tuple[int, list[dict]]:
    """0-100 : à quel point les DONNÉES sont fiables (pas l'intérêt commercial). Composants listés pour l'explication."""
    status = p.get("website_status")
    comp: list[dict] = []

    def c(key: str, label: str, pts: float, mx: float) -> None:
        comp.append({"key": key, "label": label, "points": round(pts, 1), "max": mx})
    c("identity", "entreprise identifiée par SIRET", 10 if p.get("siret") else 0, 10)
    geo, bsc = p.get("geo_confidence"), p.get("business_status_confidence")
    c("location_status", "localisation et statut actif vérifiés", 5 * float(geo if geo is not None else 0.5) + 5 * float(bsc if bsc is not None else 0.5), 10)
    conf = float(p.get("website_confidence") or 0)
    absence = p.get("website_absence_confidence")
    site_pts = {"CONFIRMED": 35, "PROBABLE": 27, "UNREACHABLE": 20, "UNCERTAIN": 8}.get(status, 0.0)
    if status == "NOT_FOUND":
        site_pts = 35 * 0.6 * float(absence if absence is not None else 0.3)        # une absence ne vaut jamais une preuve : coefficient 0,6
    c("website", f"site : {status or 'non recherché'}" + (f" (confiance {conf:.2f})" if status in ("CONFIRMED", "PROBABLE", "UNCERTAIN") else ""), site_pts, 35)
    audited = p.get("audited_at") is not None or p.get("modernization_opportunity") is not None
    c("audit", "audit du site réalisé" + (" (performance non mesurée)" if p.get("performance_status") == "UNKNOWN" else ""),
      (15 - (3 if p.get("performance_status") == "UNKNOWN" else 0)) if (audited and status in SITE_OK) else 0, 15)
    cc = p.get("contact_confidence")
    c("contact", "coordonnées lues sur le site officiel", 20 * float(cc) if cc is not None else 0, 20)
    ch = p.get("chain_confidence")
    c("chain", "réseau / chaîne évalué d'après le site" if p.get("chain_evidence") is not None or status in SITE_OK else "réseau évalué d'après SIRENE seul",
      10 if status in SITE_OK else 4, 10)
    return int(round(min(100, sum(x["points"] for x in comp)))), comp


def objective_need(p: dict) -> str | None:
    """Besoin potentiel OBSERVABLE (jamais « site non trouvé » seul) : libellé de la preuve, ou None."""
    status = p.get("website_status")
    if status == "UNREACHABLE":
        return "site identifié mais inaccessible (constaté)"
    if status in SITE_OK and (p.get("audited_at") is not None or p.get("modernization_opportunity") is not None):
        if p.get("modernization_opportunity") in ("HIGH", "MEDIUM"):
            return f"modernisation {p['modernization_opportunity']} (indices objectifs)"
        if (p.get("seo_opportunity_score") or 0) >= 50:
            return "bases SEO techniques manquantes"
        if p.get("performance_score") is not None and p["performance_score"] < 50:
            return "page d'accueil lente (mesure légère)"
        if any(i.get("severity") in ("high", "medium") for i in (p.get("issues") or [])):
            return "problèmes techniques objectifs détectés"
    return None


def _seo_term(p: dict) -> tuple[float, str]:
    seo_opp = p.get("seo_opportunity_score")
    perf = p.get("performance_score")
    lh = p.get("lighthouse_performance")
    perf_term = (1 - perf / 100) if perf is not None else 0.3
    if lh is not None:
        perf_term = 0.5 * perf_term + 0.5 * (1 - lh / 100)
    seo_term = (seo_opp / 100) if seo_opp is not None else 0.4
    return 0.6 * seo_term + 0.4 * perf_term, f"SEO à améliorer {seo_opp if seo_opp is not None else '?'}/100 · performance {perf if perf is not None else '?'}/100" + (f" · Lighthouse {lh}/100 (signal)" if lh is not None else "")


def _site_factor_scale(p: dict) -> float:
    """NOT_FOUND avec faible couverture de recherche ≠ NOT_FOUND avec forte couverture."""
    a = p.get("website_absence_confidence")
    return 0.5 if a is None else 0.4 + 0.6 * min(1.0, float(a) / 0.85)


def score_prospect(p: dict, weights: dict | None = None, thresholds: dict | None = None, activity_web: float = 0.5,
                   radius_km: float | None = None, today: date | None = None, category_weight: float = 1.0, learning: dict | None = None,
                   focus: str = "no_site") -> dict:
    """{'score','raw','category','stage','confidence','commercial_potential','data_confidence','details','caps','summary'}.
    `p` : champs du prospect (table `local_prospects`) ; `category_weight` : poids de l'activité (réglage, 1 = neutre) ;
    `learning` : modèle appris de tes résultats (`learning.refresh`), None = aucun ajustement."""
    w = merge_weights(weights)
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    today = today or date.today()
    status = p.get("website_status")
    stage = "PRELIMINARY" if status is None else "FINAL"
    d: list[dict] = []

    def add(key: str, factor: float, detail: str) -> None:
        pts = round(max(0.0, min(1.0, factor)) * w[key], 1)
        d.append({"key": key, "label": LABELS[key], "points": pts, "max": w[key], "detail": detail})

    dc, dc_components = data_confidence(p)
    audited = p.get("audited_at") is not None or p.get("modernization_opportunity") is not None
    scale = _site_factor_scale(p)
    # 1) potentiel site
    f_high, f_med, f_low, f_none = FOCUS.get(focus, FOCUS["redesign"])
    if status == "NOT_FOUND":
        add("site_potential", scale * f_none, "aucun site officiel identifié avec assez de confiance (ne prouve pas l'absence de site ; couverture de recherche "
            + (f"{p.get('website_search_tried')}/{p.get('website_search_total')} stratégies)" if p.get("website_search_total") else "inconnue)"))
    elif status == "UNREACHABLE":
        add("site_potential", 25 / 30, "site identifié mais inaccessible")
    elif status == "UNCERTAIN":
        add("site_potential", 0.35, "un site possible existe mais son identité n'est pas assez sûre : non retenu")
    elif status in SITE_OK and audited:
        mod = p.get("modernization_opportunity") or "LOW"
        very_good = mod == "LOW" and (p.get("technical_score") or 0) >= 85 and (p.get("seo_score") or 0) >= 80
        add("site_potential", 0.05 if very_good else {"HIGH": f_high, "MEDIUM": f_med, "LOW": f_low}[mod],
            "site déjà très bon" if very_good else f"site existant, modernisation {mod} d'après des indices objectifs" + (" (priorité : refonte)" if focus == "redesign" and mod != "LOW" else ""))
    else:
        add("site_potential", 0.33, "site pas encore recherché ou pas encore audité (valeur neutre)")

    # 2) compatibilité business (poids d'activité réglable)
    fit = min(1.0, activity_web * category_weight)
    notes = [f"activité dépendante du web à {int(activity_web * 100)} %" + (f" × poids d'activité {category_weight:g}" if category_weight != 1.0 else "")]
    kind = p.get("chain_kind")
    if p.get("is_chain") or kind:
        fit = 0.0
        notes.append("chaîne ou franchise : site fourni par l'enseigne, pas une cible")
    elif p.get("employee_range") not in SMALL_EMPLOYEES and (p.get("employee_range") or "") not in ("11",):
        fit *= 0.7
        notes.append("structure plus grande qu'une TPE")
    elif p.get("employee_range") in SMALL_EMPLOYEES:
        fit = min(1.0, fit + 0.1)
        notes.append("petite structure indépendante favorisée")
    add("business_fit", 0.0 if p.get("excluded_reason") else fit, " · ".join(notes) if not p.get("excluded_reason") else f"exclu : {p['excluded_reason']}")

    # 3) SEO / technique
    if status == "NOT_FOUND":
        add("seo_technical", (0.65 if p.get("socials") else 0.8) * scale, "présence digitale faible : aucun site trouvé" + (" (réseaux sociaux seulement)" if p.get("socials") else ""))
    elif status == "UNREACHABLE":
        add("seo_technical", 0.7, "site inaccessible : présence digitale faible")
    elif status == "UNCERTAIN":
        add("seo_technical", 0.3, "site non confirmé : pas d'évaluation SEO")
    elif status in SITE_OK and audited:
        f, detail = _seo_term(p)
        add("seo_technical", f, detail)
    else:
        add("seo_technical", 0.4, "non évalué (valeur neutre)")

    # 4-7)
    dist = p.get("distance_km")
    if dist is not None and p.get("geo_confidence") is not None and float(p["geo_confidence"]) < 0.6:
        dist = None                                       # géolocalisation douteuse : jamais de distance « précise »
    add("proximity", proximity_factor(None if dist is None else float(dist)), f"à {float(dist):.1f} km" if dist is not None else "distance inconnue ou géolocalisation douteuse (valeur neutre)")
    f, note = freshness_factor(p.get("company_created_at"), p.get("bodacc"), today)
    add("freshness", f, note + " · signal seulement, pas une preuve de besoin")
    f, note = contact_factor(p)
    add("contactability", f, note)
    add("data_reliability", dc / 100, f"fiabilité des données {dc}/100 : " + " · ".join(f"{x['label']} {x['points']:g}/{x['max']}" for x in dc_components if x["points"] < x["max"])[:220])

    # ajustements : pourquoi MAINTENANT, peut-il payer, qu'est-ce qui marche pour TOI
    sigs = signals_mod.detect(p, today)
    b = signals_mod.bonus(sigs)
    d.append({"key": "buy_signals", "label": "Signaux d'achat", "points": round(b * signals_mod.BONUS_MAX, 1), "max": signals_mod.BONUS_MAX,
              "detail": " · ".join(f"{x['label']} : {x['detail']}" for x in sigs) if sigs else "aucun événement récent (reprise, ouverture, déménagement…) ni site en panne"})
    bud = budget_mod.estimate(p)
    d.append({"key": "budget", "label": "Budget probable", "points": bud["points"], "max": budget_mod.MAX_POINTS, "detail": f"{bud['level']} — {bud['detail']}"})
    lp, ld = learning_mod.adjustment(learning, p, [x["code"] for x in sigs])
    d.append({"key": "learning", "label": "Tes résultats", "points": lp, "max": learning_mod.TOTAL_MAX, "detail": ld})

    adjustment = sum(x["points"] for x in d if x["key"] in ADJUSTMENTS)
    base_total = sum(x["points"] for x in d if x["key"] not in ADJUSTMENTS)
    raw_total = max(0.0, min(100.0, base_total + adjustment))
    keys = ("site_potential", "business_fit", "seo_technical", "proximity", "freshness")
    potential = int(round(100 * sum(x["points"] for x in d if x["key"] in keys) / max(1e-9, sum(w[k] for k in keys))))
    raw = int(round(raw_total))
    caps: list[dict] = []

    def cap(limit: float, why: str, soft: bool = False) -> None:
        caps.append({"limit": int(limit), "reason": why, "soft": soft})

    excluded = bool(p.get("excluded_reason"))
    dnc = bool(p.get("do_not_contact") or p.get("status") == "DO_NOT_CONTACT")
    if excluded:
        cap(0, f"exclu : {p['excluded_reason']}")
    if dnc:
        cap(0, "ne plus contacter (demande de l'entreprise)")
    if p.get("is_chain") or kind:
        cap(CHAIN_CAP, {"NETWORK": "réseau multi-villes", "FRANCHISE": "franchise"}.get(kind, "chaîne / grosse entreprise")
            + " : site fourni par l'enseigne, pas une cible")
    if stage == "FINAL":
        if status == "NOT_FOUND":
            cap(NO_SITE_CAP, "site non trouvé (≠ absence certaine) : jamais « Très bon » sur ce seul signal", soft=True)
        if not has_contact(p):
            cap(NO_CONTACT_CAP, "aucun contact professionnel public trouvé")
            cap(th["A_CONTACTER"] - 1, "« À contacter » exige un contact professionnel ou un canal officiel clair : au mieux « À examiner »")
        if dc < DATA_CONF_FLOOR:
            cap(th["A_CONTACTER"] - 1, f"fiabilité des données trop faible ({dc}/100 < {DATA_CONF_FLOOR}) : au mieux « À examiner »")
        # « Très bon » : données fiables ET besoin observable ET contact exploitable ET activité pertinente
        need = objective_need(p) or next((f"{x['label'].lower()} ({x['detail']})" for x in sigs if x["code"] in ("takeover", "rename")), None)
        gate: list[str] = []
        if dc < DATA_CONF_MIN_TRES_BON:
            gate.append(f"fiabilité des données {dc}/100 < {DATA_CONF_MIN_TRES_BON}")
        if need is None:
            gate.append("aucun besoin potentiel objectivement observable (« site non trouvé » ne suffit pas)")
        if not (p.get("phone") or p.get("email")) or (p.get("contact_confidence") is not None and float(p["contact_confidence"]) < 0.6):
            gate.append("contact professionnel insuffisamment fiable")
        if activity_web * category_weight < 0.5:
            gate.append("activité peu dépendante du web")
        if gate:
            cap(th["TRES_BON"] - 1, "« Très bon » refusé : " + " ; ".join(gate))
        if p.get("activity_key") in naf_mod.NOISY and not signals_mod.real_signal(p, sigs, today):
            cap(NOISY_CAP, "activité très concurrentielle (beaucoup de chaînes et de sites déjà faits) gardée seulement avec un vrai signal : "
                           "aucun ici (site correct ou non vérifié, pas d'événement récent)")
    else:
        cap(PRELIMINARY_CAP, "score préliminaire : le site n'a pas encore été recherché")

    # Les plafonds « de prudence » (soft) bornent le score DE BASE ; les ajustements (signaux d'achat, budget, tes résultats) s'appliquent
    # ensuite, pour départager les prospects plafonnés. Les verrous durs (exclu, chaîne, données peu fiables, pas de contact, « Très bon »
    # sans besoin prouvé) restent infranchissables.
    base_final = min([base_total] + [c["limit"] for c in caps])
    final = int(round(max(0.0, min([100.0, base_final + adjustment] + [c["limit"] for c in caps if not c["soft"]]))))
    for c in caps:
        applied = " (appliqué)" if c["limit"] == final and raw > c["limit"] else ""
        d.append({"key": "cap", "label": "Plafond / exclusion", "points": 0, "max": 0, "limit": c["limit"],
                  "detail": f"Score brut {raw} → plafond {c['limit']}{applied} : {c['reason']}"
                            + (" (plafond du score de base : signaux d'achat, budget et tes résultats s'ajoutent ensuite)" if c["soft"] else "")})

    if excluded or dnc:
        category = "IGNORER"
    elif stage == "PRELIMINARY":
        category = None
    else:
        category = next((k for k in ("TRES_BON", "A_CONTACTER", "A_EXAMINER", "FAIBLE") if final >= th[k]), "IGNORER")
    positives = sorted((x for x in d if x["key"] != "cap" and x["points"] > 0 and x["points"] >= 0.6 * x["max"] and x["max"]), key=lambda x: -x["points"])
    return {"signals": sigs, "budget_level": bud["level"], "score": final, "raw": raw, "category": category, "stage": stage, "confidence": round(dc / 100, 2), "commercial_potential": potential,
            "data_confidence": dc, "data_confidence_details": dc_components, "caps": caps, "details": d,
            "summary": [f"+{x['points']:g} {x['label']} — {x['detail']}" for x in positives[:5]]}
