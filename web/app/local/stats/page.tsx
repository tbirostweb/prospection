import Link from "next/link";
import { PageHeader } from "@/components/LocalUI";
import { getSetting, safeRows } from "@/lib/localdb";
import { ENGINE_TIER_LABELS, ERROR_LABELS, STATUS_LABELS, WEBSITE_LABELS, engineTier } from "@/lib/local";

export const dynamic = "force-dynamic";

const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)} %` : "—");
const MESSAGE_LABELS: Record<string, string> = { sans_site: "« pas de site trouvé »", site_inaccessible: "« site inaccessible »", site_ameliorable: "« points à améliorer sur le site »",
  takeover: "reprise", rename: "changement de nom", move: "déménagement" };
const messageLabel = (k: string) => k.split("_").length > 1 && MESSAGE_LABELS[k.split("_")[0]] && !MESSAGE_LABELS[k]
  ? `${MESSAGE_LABELS[k.split("_")[0]]} + ${MESSAGE_LABELS[k.split("_").slice(1).join("_")] ?? k}` : MESSAGE_LABELS[k] ?? k;

/** Statistiques de la prospection locale. */
export default async function LocalStats() {
  const [tot] = await safeRows<any>(`SELECT COUNT(*) AS discovered, COALESCE(SUM(website_status IN ('CONFIRMED','PROBABLE')),0) AS with_site,
      COALESCE(SUM(website_status='NOT_FOUND'),0) AS no_site, COALESCE(SUM(website_status='UNCERTAIN'),0) AS uncertain,
      COALESCE(SUM(website_status='UNREACHABLE'),0) AS unreachable,
      COALESCE(SUM(category IN ('TRES_BON','A_CONTACTER')),0) AS to_contact,
      COALESCE(SUM(status IN ('CONTACTED','REPLIED','INTERESTED','WON','LOST')),0) AS contacted,
      COALESCE(SUM(status IN ('REPLIED','INTERESTED','WON')),0) AS replies, COALESCE(SUM(status='WON'),0) AS won FROM local_prospects`);
  const t = tot ?? {};
  const byActivity = await safeRows<any>(`SELECT COALESCE(activity_label,'Non classée') AS name, COUNT(*) AS n, ROUND(AVG(CASE WHEN score_stage='FINAL' THEN prospect_score END)) AS avg,
      SUM(website_status='NOT_FOUND') AS no_site, SUM(status IN ('CONTACTED','REPLIED','INTERESTED','WON','LOST')) AS contacted,
      SUM(status IN ('REPLIED','INTERESTED','WON')) AS replied, SUM(status='WON') AS won FROM local_prospects GROUP BY COALESCE(activity_label,'Non classée') ORDER BY n DESC`);
  const byCity = await safeRows<any>(`SELECT city AS name, COUNT(*) AS n FROM local_prospects WHERE city IS NOT NULL GROUP BY city ORDER BY n DESC LIMIT 12`);
  const bySource = await safeRows<any>(`SELECT source AS name, COUNT(*) AS n FROM local_prospect_sources GROUP BY source ORDER BY n DESC`);
  const byWebsite = await safeRows<any>(`SELECT website_status AS name, COUNT(*) AS contacted, SUM(status IN ('REPLIED','INTERESTED','WON')) AS replied
      FROM local_prospects WHERE status IN ('CONTACTED','REPLIED','INTERESTED','WON','LOST') AND website_status IS NOT NULL GROUP BY website_status`);
  const [fu] = await safeRows<any>(`SELECT COUNT(*) AS discovered, COALESCE(SUM(excluded_reason IS NULL AND is_chain=0 AND do_not_contact=0),0) AS eligible,
      COALESCE(SUM(website_status IS NOT NULL),0) AS searched, COALESCE(SUM(website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE')),0) AS identified,
      COALESCE(SUM(website_status='CONFIRMED'),0) AS confirmed,
      COALESCE(SUM(phone IS NOT NULL OR email IS NOT NULL OR contact_form=1),0) AS contacts,
      COALESCE(SUM(score_stage='FINAL' AND category IN ('TRES_BON','A_CONTACTER','A_EXAMINER')),0) AS qualified,
      COALESCE(SUM(category IN ('TRES_BON','A_CONTACTER')),0) AS to_contact FROM local_prospects`);
  // Parent de chaque étape : un pourcentage n'a de sens que rapporté à l'étape dont elle découle (les contacts se lisent par rapport aux sites identifiés).
  const PARENT = [-1, 0, 1, 2, 3, 3, 2, 6];
  const funnel: [string, number][] = [["Entreprises découvertes", fu?.discovered], ["Pertinentes (hors chaînes, exclus, opposition)", fu?.eligible], ["Site recherché", fu?.searched],
    ["Site identifié (confirmé, probable, inaccessible)", fu?.identified], ["Dont site confirmé (≥ 90 %)", fu?.confirmed],
    ["Contact professionnel trouvé", fu?.contacts], ["Qualifiés (Très bon, À contacter, À examiner)", fu?.qualified], ["À contacter ou mieux", fu?.to_contact]].map(([l, v]) => [l as string, Number(v ?? 0)]);
  const dailyBudget = Number(process.env.LOCAL_ENGINE_DAILY_BUDGET) || 300;
  // Tous les fournisseurs DÉCLARÉS par le worker (palier, budgets, configuré : migration 023), y compris ceux à 0 requête ou sans clé.
  const ENGINE_COLS = `engine, enabled, health, requests, failures, captchas, avg_ms, consecutive_failures, last_success_at, last_error, cooldown_until,
      window_requests, month_requests, window_start + INTERVAL 24 HOUR AS window_end, (cooldown_until IS NOT NULL AND cooldown_until > UTC_TIMESTAMP()) AS cooling,
      (window_start IS NOT NULL AND window_start > UTC_TIMESTAMP() - INTERVAL 24 HOUR) AS window_fresh,
      (month_start IS NOT NULL AND month_start >= DATE(DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01'))) AS month_fresh`;
  let engineRows = await safeRows<any>(`SELECT ${ENGINE_COLS}, tier, daily_budget, monthly_budget, configured, config_note FROM local_engine_health`);
  if (engineRows.length === 0) engineRows = await safeRows<any>(`SELECT ${ENGINE_COLS} FROM local_engine_health`);   // migration 023 pas encore jouée
  const engines = engineRows.map((e) => {
    const tier = engineTier(e), daily = Number(e.daily_budget ?? (tier === 0 ? dailyBudget : 0)), monthly = Number(e.monthly_budget ?? 0);
    const used24 = e.window_fresh ? Number(e.window_requests ?? 0) : 0, usedMonth = e.month_fresh ? Number(e.month_requests ?? 0) : 0;
    const configured = e.configured === null || e.configured === undefined || Number(e.configured) === 1;
    const fmt = (d: any) => new Date(d).toLocaleString("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    const [state, tone] = !configured ? [`Non configuré${e.config_note ? ` — ${e.config_note}` : ""}`, "muted"]
      : !Number(e.enabled) ? ["Désactivé", "muted"]
      : e.cooling ? [`En pause jusqu'au ${fmt(e.cooldown_until)}`, "warn"]
      : daily > 0 && used24 >= daily ? [`Budget du jour atteint (reprise le ${fmt(e.window_end)})`, "warn"]
      : monthly > 0 && usedMonth >= monthly ? ["Plafond du mois atteint", "warn"]
      : Number(e.consecutive_failures) > 0 ? ["Actif (instable)", "warn"] : ["Actif", "ok"];
    return { ...e, tier, daily, monthly, used24, usedMonth, configured, state, tone };
  }).sort((a, b) => a.tier - b.tier || Number(b.configured) - Number(a.configured) || Number(b.health ?? 60) - Number(a.health ?? 60) || String(a.engine).localeCompare(b.engine));
  const errs = await safeRows<any>(`SELECT category, COUNT(*) AS n, MAX(at) AS last_at FROM local_events WHERE at >= UTC_TIMESTAMP() - INTERVAL 7 DAY GROUP BY category ORDER BY n DESC`);
  const [report] = await safeRows<any>(`SELECT at, problems, TIMESTAMPDIFF(MINUTE, at, UTC_TIMESTAMP()) AS age_min FROM local_health_report WHERE id=1`);
  const problems: any[] = report ? (typeof report.problems === "string" ? JSON.parse(report.problems) : report.problems) ?? [] : [];
  const sources = await safeRows<any>(`SELECT s.source, COUNT(*) AS n, MAX(s.seen_at) AS last_at,
      (SELECT COUNT(*) FROM local_events e WHERE e.source = IF(s.source='websearch','search',s.source) AND e.at >= UTC_TIMESTAMP() - INTERVAL 7 DAY) AS errs
      FROM local_prospect_sources s GROUP BY s.source`);
  const learn = await getSetting<any>("local_learning", null);
  const learned = learn ? Object.values(learn.features ?? {}).filter((f: any) => f.n >= 4).sort((a: any, b: any) => b.points - a.points) as any[] : [];
  const cards: [string, any, string?][] = [
    ["Entreprises découvertes", t.discovered ?? 0], ["Avec site vérifié", t.with_site ?? 0, `${t.uncertain ?? 0} incertain(s) · ${t.unreachable ?? 0} inaccessible(s)`],
    ["Site non trouvé", t.no_site ?? 0, "ne prouve pas l'absence de site"], ["À contacter", t.to_contact ?? 0], ["Contactés", t.contacted ?? 0],
    ["Réponses", t.replies ?? 0, `taux de réponse ${pct(Number(t.replies ?? 0), Number(t.contacted ?? 0))}`], ["Clients obtenus", t.won ?? 0],
  ];
  const Table = ({ title, rows, cols }: { title: string; rows: any[]; cols: [string, (r: any) => any][] }) => (
    <section className="panel mb-10 min-w-0">
      <h2 className="section-title">{title}</h2>
      <div className="overflow-x-auto">
      <table className="w-full text-left text-sm"><thead><tr className="mono !text-[10px]"><th className="py-2 pr-3 font-semibold">Nom</th>{cols.map(([h]) => <th key={h} className="whitespace-nowrap px-2 py-2 text-right font-semibold">{h}</th>)}</tr></thead>
        <tbody>{rows.map((r) => <tr key={r.name} className="border-t border-line"><td className="py-2.5 pr-3">{r.name}</td>{cols.map(([h, f]) => <td key={h} className="whitespace-nowrap px-2 py-2.5 text-right font-mono text-xs">{f(r)}</td>)}</tr>)}
          {rows.length === 0 && <tr><td colSpan={cols.length + 1} className="py-3 text-muted">Pas encore de données.</td></tr>}</tbody></table>
      </div>
    </section>
  );
  return (
    <div className="max-w-4xl">
      <PageHeader title="Statistiques" kicker="05 — Résultats" intro="Tes résultats, et l'état technique de la recherche (utile seulement si quelque chose ne marche pas).">
        <Link href="/settings" className="btn-ghost btn-sm">← Réglages</Link>
      </PageHeader>
      <div className="stat-grid mb-14 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
        {cards.map(([label, v, sub], i) => (
          <div key={label} className={`stat-block ${i === 3 ? "stat-block-accent" : ""}`}><div className="big-number text-[clamp(2.5rem,8vw,4.5rem)]">{v}</div>
            <div className="mono mt-3">{label}</div>{sub && <div className={`mt-1 font-mono text-[11px] ${i === 3 ? "text-white" : "text-night-dim"}`}>{sub}</div>}</div>
        ))}
      </div>
      <section className="panel mb-14 min-w-0">
        <h2 className="section-title">Entonnoir : où perd-on des prospects ?</h2>
        {/* Une étape par ligne : libellé et nombre, puis la barre sur toute la largeur (aucune largeur fixe : rien ne déborde sur mobile). */}
        <ol className="flex flex-col">{funnel.map(([label, n], i) => {
          const top = funnel[0][1] || 1;
          const parent = i > 0 && funnel[PARENT[i]][1] ? `${Math.round((100 * n) / funnel[PARENT[i]][1])} % de « ${funnel[PARENT[i]][0].split(" (")[0].toLowerCase()} »` : null;
          return (<li key={label} className="min-w-0 border-b border-line py-3 text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0">{label}</span>
              <span className="big-number shrink-0 text-xl">{n}</span>
            </div>
            <div className="mt-2 h-3 w-full border-2 border-ink bg-surface"><div className="h-full bg-signal" style={{ width: `${Math.max(1, (100 * n) / top)}%` }} /></div>
            {parent && <div className="mt-1.5 font-mono text-xs text-muted">{parent}</div>}
          </li>);
        })}</ol>
      </section>
      <section className="panel mb-14 min-w-0">
        <h2 className="section-title">Ce que tes résultats m'apprennent</h2>
        {!learn || !learn.outcomes ? (
          <p className="text-sm text-muted">Pas encore de résultat. Après chaque contact, marque « Réponse reçue », « Client obtenu », « Pas intéressant » ou « Sans réponse » :
            dès {learn?.min_outcomes ?? 10} résultats (dont {learn?.min_successes ?? 3} réussites), le score favorise ce qui marche pour toi.</p>
        ) : (
          <>
            <p className={`mb-3 text-sm ${learn.active ? "text-emerald-800" : "text-amber-900"}`}>
              {learn.outcomes} résultat(s), {learn.successes} réussite(s), taux moyen {Math.round(100 * learn.prior)} % —{" "}
              {learn.active ? "l'apprentissage ajuste les scores (±10 points au plus, expliqué sur chaque fiche)." : `apprentissage pas encore actif (il faut ${learn.min_outcomes} résultats dont ${learn.min_successes} réussites).`}
              {" "}Ces résultats sont conservés même après « Tout effacer ».</p>
            {learned.length > 0 && <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="mono !text-[10px]"><th className="px-2 py-2">Caractéristique</th><th className="px-2">Réussites</th><th className="px-2">Effet sur le score</th></tr></thead>
              <tbody>{learned.map((f) => <tr key={f.label} className="border-t border-line"><td className="px-2 py-1.5">{f.label}</td><td className="px-2 font-mono text-xs">{f.wins}/{f.n}</td>
                <td className={`px-2 font-mono text-xs ${f.points > 0 ? "text-emerald-700" : f.points < 0 ? "text-red-700" : "text-muted"}`}>{f.points > 0 ? "+" : ""}{f.points || "0 (pas assez marqué)"}</td></tr>)}</tbody></table></div>}
            {Object.keys(learn.messages ?? {}).length > 0 && <div className="mt-4"><div className="mono mb-1">Quel message obtient des réponses ?</div>
              {Object.entries(learn.messages).map(([k, m]: any) => <div key={k} className="flex justify-between gap-3 border-b border-line py-2 text-sm"><span>Brouillon {messageLabel(k)}</span><span className="font-mono text-xs">{m.wins}/{m.n} · {pct(m.wins, m.n)}</span></div>)}</div>}
          </>
        )}
      </section>
      <Table title="Répartition par activité et taux de réponse" rows={byActivity} cols={[["Entreprises", (r) => r.n], ["Sans site", (r) => r.no_site],
        ["Contactés", (r) => r.contacted], ["Réponses", (r) => `${r.replied} · ${pct(Number(r.replied), Number(r.contacted))}`], ["Clients", (r) => r.won]]} />
      <Table title="Réponses selon l'état du site (à quoi répondent-ils vraiment ?)" rows={byWebsite.map((r) => ({ ...r, name: WEBSITE_LABELS[r.name] ?? r.name }))}
        cols={[["Contactés", (r) => r.contacted], ["Réponses", (r) => `${r.replied} · ${pct(Number(r.replied), Number(r.contacted))}`]]} />
      <div className="grid gap-x-8 md:grid-cols-2">
        <Table title="Par commune" rows={byCity} cols={[["Entreprises", (r) => r.n]]} />
        <Table title="Source des entreprises" rows={bySource} cols={[["Entreprises", (r) => r.n]]} />
      </div>
      <section className="panel mb-10 min-w-0">
        <h2 className="section-title">État technique (moteurs de recherche)</h2>
        {!report ? <p className="text-sm text-amber-900">Aucun contrôle de surveillance encore effectué (le worker le lance toutes les heures).</p>
          : <p className="mono mb-3">Dernier contrôle il y a {report.age_min} min{report.age_min > 180 ? " — LA SURVEILLANCE NE TOURNE PLUS ?" : ""}</p>}
        {problems.length === 0 && report && <p className="mb-3 text-sm text-emerald-800">✓ Aucune dégradation détectée.</p>}
        {problems.map((pr, i) => <p key={i} className={`mb-2 border-2 px-3 py-2 text-sm ${pr.level === "critical" ? "border-red-200 bg-red-50 text-red-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}>{pr.level === "critical" ? "❌" : "⚠️"} {pr.message}</p>)}
        <p className="mb-3 text-xs text-muted">Gratuit d'abord ; « Gratuit à quota » seulement si aucun moteur gratuit n'a pu répondre ; « Payant » en dernier recours. Liste déclarée par le worker (mise à jour à chaque recherche et chaque heure).</p>
        <div className="flex flex-col border-t border-line">{engines.map((e) => (
          <div key={e.engine} className={`min-w-0 border-b border-line py-3 text-sm ${e.configured ? "" : "text-muted"}`}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span className="font-bold">{e.engine} <span className="mono ml-1 !text-[10px]">{ENGINE_TIER_LABELS[e.tier] ?? `Palier ${e.tier}`}</span></span>
              <span className={e.tone === "ok" ? "text-emerald-800" : e.tone === "warn" ? "text-amber-900" : "text-muted"}>{e.state}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-xs">
              <span>24 h : {e.used24}{e.daily > 0 ? ` / ${e.daily}` : ""}</span>
              <span>mois : {e.usedMonth}{e.monthly > 0 ? ` / ${e.monthly}` : " (pas de plafond)"}</span>
              {e.configured && <span className="text-muted">santé {e.health ?? "—"} · erreurs {e.requests ? `${Math.round((100 * e.failures) / e.requests)} %` : "—"}{e.captchas ? ` · ${e.captchas} captcha` : ""}{e.avg_ms ? ` · ${e.avg_ms} ms` : ""} · dernier succès {e.last_success_at ? new Date(e.last_success_at).toLocaleString("fr-FR", { timeZone: "Europe/Paris" }) : "jamais"}</span>}
            </div>
          </div>))}
          {engines.length === 0 && <p className="text-sm text-muted">Aucun moteur déclaré pour l'instant : le worker les inscrit à sa prochaine recherche ou à son contrôle horaire.</p>}
        </div>
        <div className="mt-8 grid gap-8 md:grid-cols-2">
          <div><div className="mono mb-1">Sources</div>{sources.map((s) => <div key={s.source} className="flex flex-wrap justify-between gap-x-3 border-b border-line py-2 text-sm"><span>{s.source}</span><span className="font-mono text-xs">{s.n} prospects · {Number(s.errs) ? `${s.errs} erreur(s) 7 j` : "sans erreur 7 j"}</span></div>)}</div>
          <div><div className="mono mb-1">Erreurs des 7 derniers jours</div>{errs.length === 0 ? <p className="text-sm text-muted">Aucune.</p> : errs.map((e) => <div key={e.category} className="flex justify-between gap-3 border-b border-line py-2 text-sm"><span>{ERROR_LABELS[e.category] ?? e.category}</span><span className="font-mono text-xs">{e.n}</span></div>)}</div>
        </div>
      </section>
      <p className="mono">Statuts suivis : {Object.values(STATUS_LABELS).join(" · ")}. Les taux de réponse ne deviennent significatifs qu'avec de nombreux contacts.</p>
    </div>
  );
}
