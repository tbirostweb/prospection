import Link from "next/link";
import { notFound } from "next/navigation";
import { queryOne, query } from "@/lib/db";
import { CHAIN_KIND_LABELS, EMAIL_KIND_LABELS, ERROR_LABELS, ISSUE_SEVERITY_CLASS, SITE_KIND_CLASS, SITE_OK, WEBSITE_LABELS, bestTime, distanceLabel, followupDue, formatPhone, simpleStatus, siteInfo, verifiedSocials } from "@/lib/local";
import FollowupButtons from "@/components/FollowupButtons";
import { salesBrief } from "@/lib/pitch";
import { getSetting } from "@/lib/localdb";
import type { Sender } from "@/lib/draft";
import { dateFr } from "@/lib/format";
import { parseId, safeHref } from "@/lib/security";
import ProspectActions from "./actions";
import DraftBox from "./draft";

export const dynamic = "force-dynamic";
const j = (v: any) => (typeof v === "string" ? (() => { try { return JSON.parse(v); } catch { return null; } })() : v);
const SOURCE_LABELS: Record<string, string> = { sirene: "SIRENE", sirene_bulk: "SIRENE (export)", bodacc: "BODACC", websearch: "Recherche du site", osm: "OpenStreetMap" };
const pct = (v: any) => (v == null ? "–" : `${Math.round(Number(v) * 100)} %`);

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="grid gap-1.5 border-b border-line py-4 sm:grid-cols-[160px_1fr] sm:gap-4"><dt className="mono pt-0.5">{label}</dt><dd className="min-w-0 break-words text-sm">{children}</dd></div>;
}

/** Fiche entreprise : a-t-elle un site, comment la contacter, où j'en suis. Les détails techniques sont repliés en bas. */
export default async function ProspectPage({ params }: { params: Promise<{ id: string }> }) {
  const id = parseId((await params).id);
  if (id === null) notFound();
  const p = await queryOne<any>("SELECT * FROM local_prospects WHERE id=?", [Number(id)]).catch(() => null);
  if (!p) notFound();
  const sources = await query<any>("SELECT source, source_ref, seen_at FROM local_prospect_sources WHERE prospect_id=? ORDER BY seen_at", [p.id]).catch(() => []);
  const sd = j(p.score_details) ?? { details: [], summary: [], caps: [] };
  const issues: any[] = j(p.issues) ?? [];
  const ev = j(p.website_evidence) ?? {};
  const osm = j(p.osm_data);
  const name = (p.trade_name || p.company_name).replace(/\s+/g, " ");
  const siteOk = SITE_OK.includes(p.website_status);
  const site = siteInfo(p);
  const candidate = p.website_status === "UNCERTAIN" ? (ev.candidates ?? []).find((c: any) => c.confidence >= 0.5) : null;
  const evLabel = (e: any) => (typeof e === "string" ? e : `${e.label}${e.weight ? ` (${e.weight > 0 ? "+" : ""}${e.weight})` : ""}`);
  const socials = verifiedSocials(p);
  const brief = salesBrief(JSON.parse(JSON.stringify(p)), await getSetting<Sender>("local_sender", {}));
  const due = followupDue({ status: p.status, contacted_at: p.contacted_at ? String(p.contacted_at) : null, followups: p.followups });

  return (
    <div className="max-w-3xl">
      <Link href="/local/contact" className="mono mb-8 inline-flex min-h-[44px] items-center gap-1 hover:text-accent">← À contacter</Link>
      <header className="page-header">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mono">{simpleStatus(p)}</span>
          {p.chain_kind && <span className="chip !text-[10px] !py-0">{CHAIN_KIND_LABELS[p.chain_kind]}</span>}
        </div>
        <h1 className="page-title mt-3 !text-[clamp(2.25rem,7vw+0.5rem,4.5rem)]">{name}</h1>
        <div className="page-intro !mt-4">{[p.activity_label ?? "Activité non classée", p.address ?? p.city, p.distance_km != null ? distanceLabel(p.distance_km) : null].filter(Boolean).join(" · ")}</div>
        {p.excluded_reason && <p className="mt-2 text-sm font-medium text-red-700">Écartée : {p.excluded_reason}</p>}
      </header>

      {p.pipeline_stage === "ERROR" && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          La vérification de cette entreprise a échoué ({ERROR_LABELS[p.error_category] ?? p.error_category})
          {p.next_retry_at ? ` : nouvel essai après ${dateFr(p.next_retry_at)}.` : " : plus de nouvel essai automatique."}
        </div>
      )}

      {due && (
        <section className="mb-8 flex flex-wrap items-center justify-between gap-3 border-y border-ink py-4 text-sm">
          <span><b>{due.label}</b>{p.followups ? ` · déjà relancé ${p.followups} fois` : ""}</span>
          <FollowupButtons id={p.id} kind={due.kind} />
        </section>
      )}

      <section className="mb-12">
        <dl>
          <Row label="Site web">
            <span className={`border px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ${SITE_KIND_CLASS[site.kind]}`}>{site.label}</span>
            {safeHref(p.website_url) && siteOk && <div className="mt-1"><a href={safeHref(p.website_url)!} target="_blank" rel="noopener noreferrer" className="break-all font-medium text-accent hover:underline">{p.website_url} ↗</a></div>}
            {p.website_url && p.website_status === "UNREACHABLE" && <div className="mt-1 text-muted">{p.website_url} (ne répond pas)</div>}
            {p.website_status === "PROBABLE" && <div className="mt-1 text-xs text-muted">Très probablement le sien : vérifie d'un coup d'œil.</div>}
            {candidate && safeHref(candidate.url) && <div className="mt-1 text-xs text-amber-900">Site possible, pas assez sûr pour être retenu : <a href={safeHref(candidate.url)!} target="_blank" rel="noopener noreferrer" className="underline">{candidate.url}</a></div>}
            {site.kind === "no" && <div className="mt-1 text-xs text-muted">Aucun site trouvé après recherche, ce n'est pas une certitude.</div>}
          </Row>
          <Row label="Contact">
            {p.phone || p.email || p.contact_page || socials.length ? (
              <div className="flex flex-col">
                {p.phone && <a href={`tel:${p.phone}`} className="inline-flex min-h-[44px] items-center font-medium text-accent hover:underline md:min-h-8">☎ {formatPhone(p.phone)}</a>}
                {p.email && <span><a href={`mailto:${p.email}`} className="inline-flex min-h-[44px] items-center break-all font-medium text-accent hover:underline md:min-h-8">✉ {p.email}</a> <span className="text-xs text-muted">({EMAIL_KIND_LABELS[p.email_kind] ?? "adresse professionnelle"})</span></span>}
                {safeHref(p.contact_page) && <a href={safeHref(p.contact_page)!} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-[44px] items-center font-medium text-accent hover:underline md:min-h-8">▤ {p.contact_form ? "Formulaire de contact" : "Page contact"} ↗</a>}
                {socials.filter((x) => safeHref(x.url)).map((x) => <a key={x.url} href={safeHref(x.url)!} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-[44px] items-center font-medium text-accent hover:underline md:min-h-8">{x.network} ↗</a>)}
              </div>
            ) : <span className="text-muted">Aucun contact trouvé (passer sur place reste possible).</span>}
          </Row>
          <Row label="À faire"><b>{brief.action.label}</b>{brief.action.detail ? <span className="text-ink/70"> — {brief.action.detail}</span> : null}
            {!String(brief.action.detail ?? "").includes(bestTime(p.activity_key)) && p.phone && <div className="text-xs text-muted">Quand appeler : {bestTime(p.activity_key)}</div>}
            {osm?.opening_hours ? <div className="text-xs text-muted">Horaires (OpenStreetMap) : {osm.opening_hours}</div> : null}</Row>
        </dl>
      </section>

      <ProspectActions id={p.id} status={p.status} notes={p.notes} doNotContact={!!p.do_not_contact} websiteUrl={p.website_url} siteAccepted={siteOk} feedbackReason={p.feedback_reason} />
      <DraftBox id={p.id} email={p.email} subject={p.draft_subject} body={p.draft_message} createdAt={p.draft_created_at ? String(p.draft_created_at) : null} doNotContact={!!p.do_not_contact} />

      <details className="mt-12 border-t-[3px] border-ink pt-2">
        <summary className="mono flex min-h-[44px] cursor-pointer items-center">Détails techniques (score, preuves, sources)</summary>
        <div className="mt-4 space-y-5 text-sm">
          <div>
            <div className="mono mb-1">Entreprise</div>
            <p className="text-ink/80">{p.company_name}{p.trade_name ? ` · ${p.trade_name}` : ""} · SIRET {p.siret ?? "—"}{p.naf_code ? ` · NAF ${p.naf_code}` : ""}
              {p.company_created_at ? ` · créée le ${dateFr(p.company_created_at)}` : ""} · sources : {sources.map((s: any) => SOURCE_LABELS[s.source] ?? s.source).join(", ") || "—"}</p>
          </div>
          <div>
            <div className="mono mb-1">Score {p.prospect_score ?? "–"} / 100{p.score_stage === "PRELIMINARY" ? " (préliminaire)" : ""} — calculé par des règles</div>
            <div className="overflow-x-auto"><table className="w-full text-sm"><tbody>
              {(sd.details ?? []).map((d: any, i: number) => (
                <tr key={i} className={`border-t border-line align-top ${d.key === "cap" ? "bg-amber-50/60" : ""}`}>
                  <td className="py-1.5 pr-3 font-mono text-xs">{d.key === "cap" ? "⚠" : `${d.points} / ${d.max}`}</td>
                  <td className="py-1.5 pr-3 font-medium">{d.label}</td>
                  <td className="py-1.5 text-ink/80">{d.detail}</td>
                </tr>))}
            </tbody></table></div>
          </div>
          <div>
            <div className="mono mb-1">Recherche du site — {p.website_status ? WEBSITE_LABELS[p.website_status] : "pas encore faite"}{p.website_confidence != null ? `, confiance ${pct(p.website_confidence)}` : ""}</div>
            {p.website_search_total ? <p className="text-ink/80">{p.website_search_tried}/{p.website_search_total} recherches essayées{p.website_status === "NOT_FOUND" ? ` · confiance d'absence ${pct(p.website_absence_confidence)}` : ""}</p> : null}
            {(ev.evidence ?? []).length > 0 && <ul className="list-disc pl-5 text-ink/80">{ev.evidence.map((e: any, i: number) => <li key={i}>{evLabel(e)}</li>)}</ul>}
            {ev.degraded && <p className="text-xs text-amber-900">Recherche dégradée (un moteur était en échec).</p>}
            {(ev.queries ?? []).length > 0 && <p className="mono !text-[10px]">Requêtes : {ev.queries.join(" · ")}</p>}
          </div>
          {(p.email || p.phone) && <p className="text-ink/80">Contact trouvé sur {p.contact_source_url ?? "—"} le {dateFr(p.contact_discovered_at)} (confiance {pct(p.contact_confidence)}).</p>}
          {issues.length > 0 && <div><div className="mono mb-1">Points relevés sur le site</div>
            <ul className="flex flex-col gap-1.5">{issues.map((i, k) => <li key={k} className={`rounded-lg border px-3 py-1.5 ${ISSUE_SEVERITY_CLASS[i.severity] ?? ""}`}>{i.label}</li>)}</ul></div>}
          {p.pipeline_stage === "ERROR" && p.last_error && <p className="mono !text-[10px]">Erreur : {p.last_error} ({p.attempts} tentative(s))</p>}
        </div>
      </details>
    </div>
  );
}
