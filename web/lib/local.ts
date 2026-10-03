// Prospection LOCALE : constantes et libellés partagés (miroir de worker/local/*.py — un test vérifie la cohérence).
// Un prospect local n'a exprimé AUCUN besoin ; on ne dit jamais qu'il « cherche un développeur ».

export const LOCAL_WEIGHTS = {
  site_potential: 30, business_fit: 20, seo_technical: 20, proximity: 10, freshness: 5, contactability: 10, data_reliability: 5,
} as const;

export const LOCAL_WEIGHT_LABELS: Record<string, string> = {
  site_potential: "Potentiel site", business_fit: "Compatibilité business", seo_technical: "Opportunité SEO / technique",
  proximity: "Localisation", freshness: "Fraîcheur de l'entreprise", contactability: "Contactabilité", data_reliability: "Fiabilité des données",
};

export const LOCAL_THRESHOLDS = { TRES_BON: 80, A_CONTACTER: 62, A_EXAMINER: 45, FAIBLE: 30 } as const;

export const CATEGORY_LABELS: Record<string, string> = {
  TRES_BON: "🔥 Très bon prospect", A_CONTACTER: "🟢 À contacter", A_EXAMINER: "🟡 À examiner", FAIBLE: "⚪ Faible priorité", IGNORER: "🚫 Ignorer",
};

export function categoryClass(c: string | null): string {
  switch (c) {
    case "TRES_BON": return "bg-accent text-white border-transparent";
    case "A_CONTACTER": return "bg-emerald-100 text-emerald-800 border-emerald-200";
    case "A_EXAMINER": return "bg-amber-100 text-amber-800 border-amber-200";
    default: return "bg-paper/50 text-muted border-line";
  }
}

// « Site non trouvé » ne veut PAS dire « cette entreprise n'a pas de site ».
export const WEBSITE_LABELS: Record<string, string> = {
  CONFIRMED: "Site confirmé", PROBABLE: "Site probable", UNCERTAIN: "Site incertain", NOT_FOUND: "Site non trouvé", UNREACHABLE: "Site inaccessible",
};
export const SITE_OK = ["CONFIRMED", "PROBABLE"];
export const EMAIL_KIND_LABELS: Record<string, string> = {
  GENERIC_BUSINESS: "adresse générique professionnelle", PERSONAL_BUSINESS: "adresse nominative professionnelle", UNCERTAIN: "catégorie incertaine",
};
export const CHAIN_KIND_LABELS: Record<string, string> = { NATIONAL: "chaîne nationale", NETWORK: "réseau multi-villes", FRANCHISE: "franchise (écartée)" };
export const DISLIKE_REASONS: [string, string][] = [
  ["not_relevant", "Pas pertinent"], ["site_ok", "Site finalement bon"], ["wrong_site", "Mauvais site associé"], ["chain", "Chaîne"],
  ["agency_client", "Déjà client d'une agence"], ["no_need", "Aucun besoin évident"], ["no_contact", "Contact impossible"], ["other", "Autre"],
];
export const ERROR_LABELS: Record<string, string> = {
  SEARCH_RATE_LIMIT: "Recherche limitée (429)", SEARCH_CAPTCHA: "Moteur bloqué (CAPTCHA)", SEARCH_UNAVAILABLE: "Recherche indisponible", SEARCH_TIMEOUT: "Recherche trop lente",
  SITE_TIMEOUT: "Site trop lent", SITE_DNS_ERROR: "Site : DNS introuvable", SITE_BLOCKED: "Site bloque l'accès", SITE_ROBOTS_DENIED: "Site : robots.txt refuse",
  SITE_HTTP_ERROR: "Site : erreur HTTP", AUDIT_PARSE_ERROR: "Audit : page illisible", CONTACT_NOT_FOUND: "Aucun contact trouvé", SOURCE_UNAVAILABLE: "Source indisponible",
  GEO_FAILED: "Géolocalisation impossible", DB_ERROR: "Base momentanément indisponible", UNEXPECTED: "Erreur inattendue",
};
export const LOCAL_SITE_DEFAULTS = { confirmed: 0.9, probable: 0.8, uncertain: 0.6 } as const;

// Paliers de recherche (miroir de worker/local/engines.py, test de parité) : 0 = moteurs SearXNG, 1 = API gratuites à quota, 2 = payant.
// Le palier, les budgets et « configuré » sont DÉCLARÉS par le worker dans local_engine_health ; cette table ne sert que de secours (migration 023 absente).
export const ENGINE_TIER_LABELS: Record<number, string> = { 0: "Gratuit", 1: "Gratuit à quota", 2: "Payant" };
export const API_ENGINE_TIERS: Record<string, number> = { "brave_api": 1, "tavily": 1, "serper": 2 };
export const engineTier = (r: { engine: string; tier?: number | null }) => r.tier ?? API_ENGINE_TIERS[r.engine] ?? 0;

export const STATUS_LABELS: Record<string, string> = {
  DISCOVERED: "Découvert", ENRICHED: "Enrichi", AUDITED: "Audité", QUALIFIED: "Qualifié", TO_CONTACT: "À contacter", CONTACTED: "Contacté",
  REPLIED: "Réponse reçue", INTERESTED: "Intéressé", WON: "Client obtenu", LOST: "Perdu", DO_NOT_CONTACT: "Ne plus contacter",
};
export const MANUAL_STATUSES = ["QUALIFIED", "TO_CONTACT", "CONTACTED", "REPLIED", "INTERESTED", "WON", "LOST", "DO_NOT_CONTACT"] as const;

// ⭐ bon prospect · 🚫 pas intéressant · 📞 contacté · 💬 réponse reçue · 🏆 client obtenu
export const FEEDBACK_ACTIONS: { icon: string; label: string; status: string; response?: string }[] = [
  { icon: "⭐", label: "Bon prospect", status: "TO_CONTACT" },
  { icon: "🚫", label: "Pas intéressant", status: "LOST", response: "NOT_A_FIT" },
  { icon: "📞", label: "Contacté", status: "CONTACTED" },
  { icon: "💬", label: "Réponse reçue", status: "REPLIED", response: "REPLIED" },
  { icon: "🏆", label: "Client obtenu", status: "WON", response: "WON" },
];

export const LOCAL_ACTIVITIES: [string, string][] = [
  ["restaurants", "Restaurants"], ["bars", "Bars, cafés"], ["traiteurs", "Traiteurs"], ["boulangeries", "Boulangeries, pâtisseries"],
  ["coiffure", "Salons de coiffure"], ["beaute", "Instituts de beauté"], ["garages", "Garages"], ["plombiers", "Plombiers"],
  ["electriciens", "Électriciens"], ["couvreurs", "Couvreurs"], ["menuisiers", "Menuisiers"], ["peintres", "Peintres"],
  ["macons", "Maçons, gros œuvre"], ["paysagistes", "Paysagistes"], ["fleuristes", "Fleuristes"], ["commerces", "Commerces de proximité"],
  ["boutiques", "Boutiques (mode, déco, beauté…)"], ["immobilier", "Agences immobilières"], ["hebergements", "Hébergements"],
  ["services", "Services (comptables, nettoyage, blanchisserie…)"], ["associations", "Associations, clubs"], ["photographes", "Photographes"],
  ["fitness", "Salles de sport, coachs"], ["soins_personnels", "Tatoueurs, toiletteurs, soins divers"], ["auto_ecoles", "Auto-écoles"],
  ["demenageurs", "Déménageurs"], ["opticiens", "Opticiens"], ["architectes", "Architectes"], ["avocats", "Avocats"], ["veterinaires", "Vétérinaires"],
  ["sante", "Kinés, ostéos, dentistes"], ["artisans_art", "Artisans d'art (bijoux, meubles…)"], ["evenementiel", "Événementiel, spectacles"],
  ["cours", "Cours, formations"],
];

// Pré-recherches prêtes à l'emploi : des groupes d'activités cohérents, lançables en un clic. Tu peux enregistrer les tiennes (Réglages).
export interface Preset { key: string; label: string; icon: string; activities: string[]; custom?: boolean }
export const PRESETS: Preset[] = [
  { key: "best", icon: "⭐", label: "Meilleurs clients potentiels", activities: ["plombiers", "electriciens", "couvreurs", "menuisiers", "paysagistes", "garages", "immobilier", "architectes", "photographes", "demenageurs", "auto_ecoles", "beaute"] },
  { key: "bouche", icon: "🍽️", label: "Restauration & métiers de bouche", activities: ["restaurants", "bars", "traiteurs", "boulangeries"] },
  { key: "beaute", icon: "✂️", label: "Beauté & bien-être", activities: ["coiffure", "beaute", "soins_personnels", "fitness"] },
  { key: "batiment", icon: "🔨", label: "Artisans du bâtiment", activities: ["plombiers", "electriciens", "couvreurs", "menuisiers", "peintres", "macons", "paysagistes"] },
  { key: "commerces", icon: "🛍️", label: "Commerces & boutiques", activities: ["commerces", "boutiques", "fleuristes", "opticiens"] },
  { key: "auto", icon: "🚗", label: "Auto & mobilité", activities: ["garages", "auto_ecoles", "demenageurs"] },
  { key: "liberales", icon: "💼", label: "Professions libérales & services", activities: ["services", "avocats", "architectes", "photographes", "sante", "veterinaires"] },
  { key: "tourisme", icon: "🏡", label: "Tourisme & immobilier", activities: ["hebergements", "immobilier"] },
  { key: "creatifs", icon: "🎨", label: "Artisans d'art, loisirs & cours", activities: ["artisans_art", "evenementiel", "cours", "associations"] },
];
export const RADII = [5, 10, 20, 30, 50];

export const ISSUE_SEVERITY_CLASS: Record<string, string> = {
  high: "border-red-200 bg-red-50 text-red-800", medium: "border-amber-200 bg-amber-50 text-amber-900",
  low: "border-line bg-paper/40 text-ink/80", info: "border-line bg-white text-muted",
};

/** Formulations prudentes — jamais « aucun référencement » : un test isolé ne le démontre pas. */
export function seoSummary(score: number | null | undefined): string {
  if (score == null) return "SEO non évalué";
  return score < 50 ? "bases SEO manquantes" : score < 75 ? "SEO technique améliorable" : "aucun problème majeur détecté";
}

export const distanceLabel = (km: number | string | null | undefined) => (km == null ? "distance inconnue" : `${Number(km).toFixed(1).replace(".", ",")} km`);

export function monthsSince(date: string | null | undefined): number | null {
  if (!date) return null;
  const d = new Date(date);
  const now = new Date();
  return (now.getFullYear() - d.getFullYear()) * 12 + now.getMonth() - d.getMonth();
}

export interface LocalRow {
  id: number; siret: string | null; company_name: string; trade_name: string | null; activity_key: string | null; activity_label: string | null;
  city: string | null; postal_code: string | null; distance_km: number | string | null; company_created_at: string | null;
  website_url: string | null; website_status: string | null; website_confidence: number | string | null;
  seo_score: number | null; performance_score: number | null; technical_score: number | null; modernization_opportunity: string | null;
  lighthouse_performance: number | null; issues: any; prospect_score: number | null; score_stage: string | null; category: string | null;
  status: string; is_chain: number; excluded_reason: string | null; do_not_contact: number; phone: string | null; email: string | null;
  email_kind: string | null; contact_page: string | null; bodacc: any; discovered_at: string; contacted_at: string | null;
  commercial_potential_score?: number | null; data_confidence_score?: number | null; website_search_tried?: number | null; website_search_total?: number | null;
  website_absence_confidence?: number | string | null; chain_kind?: string | null; contact_confidence?: number | string | null; contact_form?: number | null;
  seo_opportunity_score?: number | null; pipeline_stage?: string | null; error_category?: string | null; entity_level?: string | null; sibling_count?: number | null;
  score_summary?: string | null; latitude?: number | string | null; longitude?: number | string | null; new_business_at?: string | null;
  draft_created_at?: string | null; last_contacted_at?: string | null;
  deep_enriched_at?: string | null; buy_signals?: any; budget_level?: string | null; followups?: number | null; manager_name?: string | null; address?: string | null; social_links?: any; notes?: string | null;
}

// « Aucun site » n'est un FAIT qu'après une vraie recherche (miroir de signals.ABSENCE_MIN).
export const ABSENCE_MIN = 0.6;
export const siteSearched = (p: { website_status?: string | null; website_absence_confidence?: any }) =>
  p.website_status === "NOT_FOUND" && Number(p.website_absence_confidence ?? 0) >= ABSENCE_MIN;
// Réponse SIMPLE à « a-t-il un site ? » pour les listes (jamais « pas de site » affirmé avant une vraie recherche).
export type SiteKind = "yes" | "down" | "no" | "check" | "pending";
export function siteInfo(p: { website_status?: string | null; website_absence_confidence?: any; website_url?: string | null }): { kind: SiteKind; label: string; url: string | null } {
  const url = p.website_url ?? null;
  switch (p.website_status) {
    case "CONFIRMED": return { kind: "yes", label: "A un site", url };
    case "PROBABLE": return { kind: "yes", label: "A un site (probable)", url };
    case "UNREACHABLE": return { kind: "down", label: "Site en panne", url };
    case "UNCERTAIN": return { kind: "check", label: "Site à vérifier", url: null };
    case "NOT_FOUND": return siteSearched(p) ? { kind: "no", label: "Pas de site trouvé", url: null } : { kind: "pending", label: "Recherche du site en cours", url: null };
    default: return { kind: "pending", label: "Site pas encore recherché", url: null };
  }
}
export const SITE_KIND_CLASS: Record<SiteKind, string> = {
  yes: "border-emerald-200 bg-emerald-50 text-emerald-800", down: "border-violet-200 bg-violet-50 text-violet-800",
  no: "border-red-200 bg-red-50 text-red-800", check: "border-amber-200 bg-amber-50 text-amber-900", pending: "border-line bg-paper/40 text-muted",
};
/** Statut lisible : où j'en suis avec cette entreprise (le statut commercial prime sur la qualification automatique). */
const COLD_STATUSES = ["DISCOVERED", "ENRICHED", "AUDITED", "QUALIFIED"];
export function simpleStatus(p: { status: string; category?: string | null; do_not_contact?: any; excluded_reason?: string | null; is_chain?: any }): string {
  if (p.do_not_contact || p.status === "DO_NOT_CONTACT") return "Ne plus contacter";
  if (p.excluded_reason || p.is_chain) return "Écartée";
  if (!COLD_STATUSES.includes(p.status)) return STATUS_LABELS[p.status] ?? p.status;
  if (p.category === "TRES_BON" || p.category === "A_CONTACTER") return "À contacter";
  if (p.category === "A_EXAMINER") return "À examiner";
  if (p.category === "FAIBLE" || p.category === "IGNORER") return "Peu intéressante";
  return "Pas encore traitée";
}
export const formatPhone = (s: string) => s.replace(/(\d{2})(?=\d)/g, "$1 ");

// Profils sociaux VÉRIFIÉS (lien sur le site officiel, ou nom + ville dans le résultat de recherche) : worker/local/socials.py.
export interface SocialLink { network: string; url: string; via: "site" | "search"; confidence: number }
export const verifiedSocials = (p: { social_links?: any }): SocialLink[] => asList(p.social_links).filter((x: any) => x?.url && x?.network);
export const NETWORK_ICONS: Record<string, string> = { Facebook: "📘", Instagram: "📸", LinkedIn: "💼", TikTok: "🎵", YouTube: "▶️", Pinterest: "📌", X: "✖️" };

// FICHE PRÊTE (miroir de worker/local/today.READY) : traitement terminé sans erreur, enrichissement fait, site tranché, au moins un contact.
export const READY_SQL = "p.pipeline_stage='DONE' AND p.deep_enriched_at IS NOT NULL AND (p.phone IS NOT NULL OR p.email IS NOT NULL OR p.contact_form=1) "
  + "AND (p.website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE') OR (p.website_status='NOT_FOUND' AND p.website_absence_confidence >= 0.6))";
export function ficheReady(p: { pipeline_stage?: string | null; deep_enriched_at?: any; phone?: string | null; email?: string | null; contact_form?: any;
  website_status?: string | null; website_absence_confidence?: any }): boolean {
  const site = ["CONFIRMED", "PROBABLE", "UNREACHABLE"].includes(p.website_status ?? "") || siteSearched(p);
  return p.pipeline_stage === "DONE" && !!p.deep_enriched_at && !!(p.phone || p.email || Number(p.contact_form)) && site;
}

// Qualité plutôt que quantité (miroir de naf.FAVORED / naf.NOISY) : les activités « bruitées » ne sont gardées qu'avec un vrai signal.
export const FAVORED_ACTIVITIES = ["plombiers", "electriciens", "couvreurs", "menuisiers", "peintres", "macons", "paysagistes", "garages", "immobilier", "architectes",
  "avocats", "sante", "veterinaires", "photographes", "demenageurs", "auto_ecoles", "hebergements", "artisans_art", "beaute", "coiffure",
  "soins_personnels", "fitness", "evenementiel", "cours", "traiteurs", "services"];
export const NOISY_ACTIVITIES = ["restaurants", "bars", "boulangeries", "commerces", "boutiques", "opticiens", "associations"];

// Signaux d'ACHAT (miroir de worker/local/signals.py) : pourquoi MAINTENANT.
export const SIGNAL_ICONS: Record<string, string> = {
  takeover: "🔑", new_no_site: "🆕", rename: "🏷️", site_down: "💥", move: "📦", site_diy: "🧱", new_establishment: "🏪", socials_only: "📱", no_contact_way: "✉️", site_builder: "🧩",
};
export function asList(v: any): any[] { return  Array.isArray(v) ? v : typeof v === "string" ? (() => { try { return JSON.parse(v) ?? []; } catch { return []; } })() : []; }
export const BODACC_EVENT_LABELS: Record<string, string> = {
  creation: "création", takeover: "reprise", move: "déménagement", rename: "changement de nom", new_establishment: "nouvel établissement", closure: "cessation", change: "modification",
};
export const BUDGET_LABELS: Record<string, string> = { "élevé": "💰 budget probable élevé", moyen: "budget moyen", "serré": "budget serré", inconnu: "budget inconnu" };

// Quand appeler ? Créneaux où le patron est joignable et disponible, selon le métier (usages courants, à ajuster avec ton expérience).
const TIME_BY_ACTIVITY: [string[], string][] = [
  [["restaurants", "bars", "traiteurs"], "entre 15 h et 17 h 30 (entre les deux services), jamais pendant le rush"],
  [["boulangeries"], "entre 14 h et 16 h (après le rush de midi)"],
  [["coiffure", "beaute", "soins_personnels"], "mardi à jeudi, 10 h – 11 h 30 ou 14 h – 15 h (jamais le samedi)"],
  [["plombiers", "electriciens", "couvreurs", "menuisiers", "peintres", "macons", "paysagistes"], "tôt (7 h 30 – 8 h 30) ou en fin de journée (17 h 30 – 19 h) : sur chantier la journée"],
  [["garages"], "8 h 30 – 10 h ou 14 h – 16 h"],
  [["commerces", "boutiques", "fleuristes", "opticiens", "artisans_art"], "mardi à jeudi, 10 h – 11 h 30 (magasin calme)"],
  [["avocats", "architectes", "sante", "veterinaires", "services"], "9 h – 10 h ou 17 h – 18 h (entre les rendez-vous)"],
  [["immobilier"], "9 h 30 – 11 h"],
  [["hebergements"], "10 h – 12 h (après les départs)"],
  [["fitness", "cours", "auto_ecoles"], "10 h – 12 h ou 14 h – 16 h (avant les cours du soir)"],
];
export function bestTime(activityKey: string | null | undefined): string {
  return TIME_BY_ACTIVITY.find(([keys]) => keys.includes(activityKey ?? ""))?.[1] ?? "mardi à jeudi, 9 h 30 – 11 h 30 ou 14 h – 16 h 30";
}

// Relances : J+3 puis J+10 après le premier contact, puis « sans réponse » à J+21.
export const FOLLOWUP_DAYS = [3, 10] as const;
export const NO_REPLY_DAYS = 21;
export function followupDue(p: { status: string; contacted_at: string | null; followups?: number | null }): { label: string; kind: "followup" | "no_reply" } | null {
  if (p.status !== "CONTACTED" || !p.contacted_at) return null;
  const days = (Date.now() - new Date(p.contacted_at).getTime()) / 86_400_000;
  const n = Number(p.followups ?? 0);
  if (n < FOLLOWUP_DAYS.length && days >= FOLLOWUP_DAYS[n]) return { label: `Relance ${n + 1} (J+${FOLLOWUP_DAYS[n]})`, kind: "followup" };
  if (n >= FOLLOWUP_DAYS.length && days >= NO_REPLY_DAYS) return { label: `Sans réponse depuis ${Math.floor(days)} jours`, kind: "no_reply" };
  return null;
}


// Pastilles de la carte et des listes : où en es-tu avec chaque prospect ?
export const PIPELINE_STAGES: { key: string; label: string; color: string }[] = [
  { key: "new", label: "Pas encore traité", color: "#9ca3af" },
  { key: "to_contact", label: "À contacter", color: "#10b981" },
  { key: "contacted", label: "Contacté", color: "#0ea5e9" },
  { key: "replied", label: "Réponse reçue", color: "#8b5cf6" },
  { key: "interested", label: "Intéressé", color: "#f59e0b" },
  { key: "won", label: "Client obtenu", color: "#eab308" },
  { key: "lost", label: "Perdu", color: "#fb7185" },
  { key: "dnc", label: "Ne plus contacter", color: "#111827" },
];
export function pipelineStage(p: { status: string; category: string | null; do_not_contact?: number | boolean }): string {
  if (p.do_not_contact || p.status === "DO_NOT_CONTACT") return "dnc";
  switch (p.status) {
    case "TO_CONTACT": return "to_contact";
    case "CONTACTED": return "contacted";
    case "REPLIED": return "replied";
    case "INTERESTED": return "interested";
    case "WON": return "won";
    case "LOST": return "lost";
    default: return p.category === "TRES_BON" || p.category === "A_CONTACTER" ? "to_contact" : "new";
  }
}
export const CATEGORY_COLORS: Record<string, string> = { TRES_BON: "#F0451E", A_CONTACTER: "#10b981", A_EXAMINER: "#f59e0b", FAIBLE: "#9ca3af", IGNORER: "#d1d5db", "": "#ffffff" };
export const SITE_COLORS: Record<string, string> = { CONFIRMED: "#10b981", PROBABLE: "#14b8a6", UNCERTAIN: "#f59e0b", NOT_FOUND: "#ef4444", UNREACHABLE: "#8b5cf6", "": "#9ca3af" };
