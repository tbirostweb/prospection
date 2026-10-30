import { NextRequest, NextResponse } from "next/server";
import { query, queryOne, transaction } from "@/lib/db";
import { readJson, routeId } from "@/lib/api";
import { DEAL_STAGES, DISLIKE_REASONS, MANUAL_STATUSES, isImported } from "@/lib/local";
import { FREEMAIL, autoIneligible } from "@/lib/autosend";
import { getAutoSend } from "@/lib/localdb";

const RESPONSES = ["", "REPLIED", "INTERESTED", "NOT_INTERESTED", "NO_ANSWER", "NOT_A_FIT", "WON"];

/** Suivi commercial d'un prospect : statut, réponse, notes, « ne plus contacter », mise en file d'envoi automatique (l'envoi lui-même : worker). */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const r = await routeId(params);
  if ("error" in r) return r.error;
  const { id } = r;
  const parsed = await readJson(req);
  if ("error" in parsed) return parsed.error;
  const b = parsed.body;
  const p = await queryOne<any>("SELECT * FROM local_prospects WHERE id=?", [id]);
  if (!p) return NextResponse.json({ error: "prospect introuvable" }, { status: 404 });

  const imported = isImported(p);
  const reviewEvidence = () => {
    const ev = typeof p.website_evidence === "string" ? (() => { try { return JSON.parse(p.website_evidence); } catch { return null; } })() : p.website_evidence;
    return JSON.stringify((ev?.evidence ?? []).map((e: any) => e?.code).filter(Boolean));
  };
  // « C'est bien son site » : ta décision confirme le site et sert à ajuster le seuil des prochains (local_site_reviews).
  if (b.action === "confirm_site") {
    if (p.do_not_contact) return NextResponse.json({ error: "entreprise en liste « ne plus contacter »" }, { status: 409 });
    if (!p.website_url) return NextResponse.json({ error: "aucun site à confirmer" }, { status: 400 });
    await transaction(async (q) => {
      await q("INSERT INTO local_site_reviews (prospect_id, url, verdict, confidence, evidence) VALUES (?,?, 'CONFIRMED', ?, ?)",
        [p.id, p.website_url, p.website_confidence, reviewEvidence()]);
      // le worker relit le site (âge, contacts manquants) puis recalcule le score
      await q(`UPDATE local_prospects SET website_status='CONFIRMED', site_review='CONFIRMED', site_reviewed_at=UTC_TIMESTAMP(),
        pipeline_stage=IF(?, 'IMPORTED', pipeline_stage), provenance=IF(?, provenance, JSON_REMOVE(COALESCE(provenance, JSON_OBJECT()), '$.contacts')) WHERE id=?`,
        [imported ? 1 : 0, imported ? 1 : 0, id]);
      if (!imported) await q(`UPDATE local_campaigns SET status='queued', enabled=1 WHERE id IN (SELECT campaign_id FROM local_prospect_campaigns WHERE prospect_id=?)`, [id]);
    });
    return NextResponse.json({ ok: true });
  }
  // Fiche importée : « Ce n'est pas son site » retire le site SEUL (e-mail et téléphone viennent du fichier, pas du site) ; aucune recherche relancée.
  if (b.action === "wrong_site" && imported) {
    const domain = p.canonical_domain || (() => { try { return new URL(p.website_url).hostname.replace(/^www\./, ""); } catch { return null; } })();
    if (!domain) return NextResponse.json({ error: "aucun site associé à dissocier" }, { status: 400 });
    await transaction(async (q) => {
      await q("INSERT IGNORE INTO local_bad_sites (siret, fingerprint, domain, reason) VALUES (?,?,?,?)", [p.siret, p.fingerprint, domain, "mauvais site (retour utilisateur)"]);
      await q("INSERT INTO local_site_reviews (prospect_id, url, verdict, confidence, evidence) VALUES (?,?, 'REJECTED', ?, ?)",
        [p.id, p.website_url, p.website_confidence, reviewEvidence()]);
      await q(`UPDATE local_prospects SET website_status=NULL, website_url=NULL, website_confidence=NULL, website_evidence=NULL, canonical_domain=NULL,
        site_year=NULL, site_review='REJECTED', site_reviewed_at=UTC_TIMESTAMP(), pipeline_stage='IMPORTED' WHERE id=?`, [id]);
    });
    return NextResponse.json({ ok: true });
  }

  // « Mauvais site » / « J'ai trouvé le site » : retours ciblés sur CE prospect (jamais d'apprentissage global).
  if (b.action === "wrong_site" || b.action === "found_site") {
    if (p.do_not_contact) return NextResponse.json({ error: "entreprise en liste « ne plus contacter »" }, { status: 409 });
    const snapshot = JSON.stringify({ company_name: p.company_name, trade_name: p.trade_name, siret: p.siret, siren: p.siren, city: p.city,
      postal_code: p.postal_code, address: p.address, naf_code: p.naf_code, previous_site: p.website_url, previous_status: p.website_status,
      previous_confidence: p.website_confidence, previous_evidence: p.website_evidence });
    if (b.action === "wrong_site") {
      const domain = p.canonical_domain || (() => { try { return new URL(p.website_url).hostname.replace(/^www\./, ""); } catch { return null; } })();
      if (!domain) return NextResponse.json({ error: "aucun site associé à dissocier" }, { status: 400 });
      await query("INSERT IGNORE INTO local_bad_sites (siret, fingerprint, domain, reason) VALUES (?,?,?,?)", [p.siret, p.fingerprint, domain, "mauvais site (retour utilisateur)"]);
      await query("INSERT INTO local_site_feedback (prospect_id, siret, kind, url, company) VALUES (?,?, 'wrong_site', ?, ?)", [p.id, p.siret, p.website_url, snapshot]);
    } else {
      let url: URL;
      try { url = new URL(String(b.url ?? "").trim().startsWith("http") ? String(b.url).trim() : `https://${String(b.url ?? "").trim()}`); } catch { return NextResponse.json({ error: "URL invalide" }, { status: 400 }); }
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname.includes(".")) return NextResponse.json({ error: "URL invalide" }, { status: 400 });
      await query("INSERT INTO local_site_feedback (prospect_id, siret, kind, url, company) VALUES (?,?, 'found_site', ?, ?)", [p.id, p.siret, url.origin + "/", snapshot]);
    }
    // Tout ce qui venait du mauvais site (contacts, audit, chaîne, score) est retiré : le worker recalcule à sa prochaine passe.
    await query(`UPDATE local_prospects SET website_status=NULL, website_url=NULL, website_confidence=NULL, website_evidence=NULL, website_original_url=NULL,
      website_final_url=NULL, canonical_domain=NULL, redirect_chain=NULL, website_search_tried=NULL, website_search_total=NULL, website_absence_confidence=NULL,
      phone=NULL, phone_confidence=NULL, email=NULL, email_kind=NULL, contact_form=0, contact_page=NULL, contact_source_url=NULL, contact_confidence=NULL,
      contact_evidence=NULL, technical_score=NULL, seo_score=NULL, seo_opportunity_score=NULL, performance_score=NULL, performance_status=NULL,
      modernization_opportunity=NULL, modernization_evidence=NULL, seo_evidence=NULL, local_presence=NULL, issues=NULL, audit=NULL, audited_at=NULL,
      lighthouse_performance=NULL, lighthouse_seo=NULL, lighthouse_accessibility=NULL, lighthouse_best_practices=NULL, lighthouse_at=NULL,
      chain_confidence=NULL, chain_kind=NULL, chain_name=NULL, chain_evidence=NULL, is_chain=0, draft_message=NULL, provenance=NULL,
      pipeline_stage='DISCOVERED', attempts=0, next_retry_at=NULL, error_category=NULL, last_error=NULL WHERE id=?`, [id]);
    await query(`UPDATE local_campaigns SET status='queued', enabled=1 WHERE id IN (SELECT campaign_id FROM local_prospect_campaigns WHERE prospect_id=?)`, [id]);
    return NextResponse.json({ ok: true, requeued: true });
  }
  // « Ajouter l'e-mail » : adresse trouvée par toi (jamais pour une entreprise qui s'est opposée). Le score est recalculé par le worker
  // (worker/local/emailsearch.py, provenance `email_manual.pending`) ; la fiche peut être mise en file d'envoi aussitôt.
  if (b.action === "set_email") {
    const email = String(b.email ?? "").trim().toLowerCase();
    if (!/^[^@\s<>"',;]+@[^@\s<>"',;]+\.[a-z]{2,}$/i.test(email) || email.length > 255) return NextResponse.json({ error: "adresse e-mail invalide" }, { status: 400 });
    if (p.do_not_contact || await queryOne<any>("SELECT 1 AS x FROM local_do_not_contact WHERE email=? LIMIT 1", [email]))
      return NextResponse.json({ error: "cette adresse ou cette entreprise a demandé à ne plus être contactée" }, { status: 409 });
    const kind = FREEMAIL.has(email.split("@")[1]) ? "UNCERTAIN" : "GENERIC_BUSINESS";
    const evidence = JSON.stringify({ kind: "email", value: email, category: kind, via: "manual", source: "ajoutée à la main", confidence: 0.95 });
    await query(`UPDATE local_prospects SET email=?, email_kind=?, contact_discovered_at=UTC_TIMESTAMP(),
      contact_confidence=GREATEST(COALESCE(contact_confidence, 0), 0.95),
      contact_evidence=JSON_ARRAY_APPEND(COALESCE(contact_evidence, JSON_ARRAY()), '$', CAST(? AS JSON)),
      provenance=JSON_SET(COALESCE(provenance, JSON_OBJECT()), '$.email_manual', JSON_OBJECT('at', DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ'), 'pending', TRUE))
      WHERE id=?`, [email, kind, evidence, id]);
    return NextResponse.json({ ok: true, email_kind: kind });
  }
  // Envoi automatique : mettre en file, retirer de la file, ou arrêter une séquence en cours (plus aucune relance).
  if (b.action === "auto_queue") {
    const why = autoIneligible(p, (await getAutoSend()).personal_emails) ?? (p.auto_state && p.auto_state !== "stopped" ? "séquence automatique déjà en cours ou terminée" : null);
    if (why) return NextResponse.json({ error: why }, { status: 409 });
    await query("UPDATE local_prospects SET auto_state='queued', auto_queued_at=UTC_TIMESTAMP() WHERE id=?", [id]);
    return NextResponse.json({ ok: true });
  }
  if (b.action === "auto_stop") {
    if (!["queued", "active"].includes(p.auto_state)) return NextResponse.json({ error: "aucun envoi automatique en cours" }, { status: 409 });
    await query("UPDATE local_prospects SET auto_state=IF(auto_state='queued', NULL, 'stopped') WHERE id=?", [id]);
    return NextResponse.json({ ok: true });
  }
  // Relances : « Relancé » (J+3, J+7) et « Sans réponse » (tu arrêtes : compté comme un échec par l'apprentissage).
  if (b.action === "followed_up" || b.action === "no_answer") {
    if (p.do_not_contact) return NextResponse.json({ error: "entreprise en liste « ne plus contacter »" }, { status: 409 });
    if (b.action === "followed_up")
      await query("UPDATE local_prospects SET followups=followups+1, last_followup_at=UTC_TIMESTAMP(), last_contacted_at=UTC_TIMESTAMP() WHERE id=?", [id]);
    else await query("UPDATE local_prospects SET status='LOST', response_status='NO_ANSWER' WHERE id=?", [id]);
    return NextResponse.json({ ok: true });
  }
  // Suivi commercial après une réponse : étape de l'affaire (avec sa date) et rappel. Rien n'est envoyé depuis ces actions.
  if (b.action === "deal_stage") {
    const stage = String(b.stage ?? "");
    if (!DEAL_STAGES.some(([k]) => k === stage)) return NextResponse.json({ error: "étape invalide" }, { status: 400 });
    if (p.do_not_contact) return NextResponse.json({ error: "entreprise en liste « ne plus contacter »" }, { status: 409 });
    // L'étape pilote le statut et la réponse existants (suivi, apprentissage). « Perdu » = pas intéressé ; gagné ou perdu : plus de rappel.
    const [status, response] = ({ REPLIED: ["REPLIED", "REPLIED"], MEETING: ["INTERESTED", "INTERESTED"], QUOTE: ["INTERESTED", "INTERESTED"],
      WON: ["WON", "WON"], LOST: ["LOST", "NOT_INTERESTED"] } as Record<string, [string, string]>)[stage];
    const closed = stage === "WON" || stage === "LOST";
    await query(`UPDATE local_prospects SET deal_stage=?, deal_stage_at=UTC_TIMESTAMP(), status=?, response_status=?,
      contacted_at=COALESCE(contacted_at, UTC_TIMESTAMP()), last_prospect_contact_at=UTC_TIMESTAMP(),
      remind_at=IF(?, NULL, remind_at), remind_note=IF(?, NULL, remind_note) WHERE id=?`, [stage, status, response, closed ? 1 : 0, closed ? 1 : 0, id]);
    return NextResponse.json({ ok: true });
  }
  // « À rappeler le » + note courte ; date vide = rappel fait / supprimé.
  if (b.action === "remind") {
    const date = b.date ? String(b.date) : null;
    if (date) {
      const d = new Date(`${date}T00:00:00Z`);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== date) return NextResponse.json({ error: "date invalide" }, { status: 400 });
      if (p.do_not_contact) return NextResponse.json({ error: "entreprise en liste « ne plus contacter »" }, { status: 409 });
    }
    await query("UPDATE local_prospects SET remind_at=?, remind_note=? WHERE id=?", [date, date ? String(b.note ?? "").trim().slice(0, 255) || null : null, id]);
    return NextResponse.json({ ok: true });
  }
  if (b.feedback_reason !== undefined) {
    if (b.feedback_reason && !DISLIKE_REASONS.some(([k]) => k === b.feedback_reason)) return NextResponse.json({ error: "raison invalide" }, { status: 400 });
    await query("UPDATE local_prospects SET feedback_reason=? WHERE id=?", [b.feedback_reason || null, id]);
  }

  if (b.status !== undefined) {
    if (!(MANUAL_STATUSES as readonly string[]).includes(b.status)) return NextResponse.json({ error: "statut invalide" }, { status: 400 });
    if (p.do_not_contact && b.status !== "DO_NOT_CONTACT")
      return NextResponse.json({ error: "cette entreprise a demandé à ne plus être contactée : la liste ne se modifie pas depuis l'interface" }, { status: 409 });
    if (b.status === "DO_NOT_CONTACT") {
      let domain: string | null = null;
      try { domain = p.website_url ? new URL(p.website_url).hostname.replace(/^www\./, "") : null; } catch { /* URL illisible */ }
      // Tout ou rien : l'opposition et le statut sont enregistrés ensemble (jamais d'état partiel).
      await transaction(async (q) => {
        await q("INSERT INTO local_do_not_contact (siret, siren, domain, email, reason) VALUES (?,?,?,?,?)",
          [p.siret, p.siren, domain, p.email, String(b.reason ?? "demande de ne plus être contacté").slice(0, 255)]);
        await q("UPDATE local_prospects SET do_not_contact=1, status='DO_NOT_CONTACT', auto_state=IF(auto_state IN ('queued','active'), 'stopped', auto_state) WHERE id=?", [id]);
      });
    } else {
      await query(`UPDATE local_prospects SET status=?,
                   followups = IF(? = 'CONTACTED' AND contacted_at IS NULL, 0, followups),
                   contacted_at = IF(? IN ('CONTACTED','REPLIED','INTERESTED','WON') AND contacted_at IS NULL, UTC_TIMESTAMP(), contacted_at),
                   last_contacted_at = IF(? = 'CONTACTED', UTC_TIMESTAMP(), last_contacted_at),
                   deal_stage = IF(? = 'WON', 'WON', deal_stage), deal_stage_at = IF(? = 'WON', UTC_TIMESTAMP(), deal_stage_at),
                   remind_at = IF(? = 'WON', NULL, remind_at), remind_note = IF(? = 'WON', NULL, remind_note) WHERE id=?`, [b.status, b.status, b.status, b.status, b.status, b.status, b.status, b.status, id]);
    }
  }
  if (b.response_status !== undefined) {
    if (!RESPONSES.includes(b.response_status)) return NextResponse.json({ error: "réponse invalide" }, { status: 400 });
    await query("UPDATE local_prospects SET response_status=?, last_prospect_contact_at=IF(? IN ('REPLIED','INTERESTED','NOT_INTERESTED','WON'), UTC_TIMESTAMP(), last_prospect_contact_at) WHERE id=?", [b.response_status || null, b.response_status || null, id]);
  }
  if (b.draft_message !== undefined || b.draft_subject !== undefined) {
    if (p.do_not_contact && b.draft_message !== null) return NextResponse.json({ error: "entreprise en liste « ne plus contacter »" }, { status: 409 });
    if (b.draft_message === null) await query("UPDATE local_prospects SET draft_message=NULL, draft_subject=NULL, draft_created_at=NULL WHERE id=?", [id]);
    else await query("UPDATE local_prospects SET draft_subject=?, draft_message=? WHERE id=?", [String(b.draft_subject ?? "").slice(0, 200), String(b.draft_message ?? "").slice(0, 10000), id]);
  }
  if (b.notes !== undefined) await query("UPDATE local_prospects SET notes=? WHERE id=?", [String(b.notes).slice(0, 5000), id]);
  return NextResponse.json({ ok: true });
}
