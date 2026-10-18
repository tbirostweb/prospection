// Import CSV : accès base (lecture du contexte, application). Le rapprochement et les règles sont dans lib/csvimport.ts (pur).
// `q` est un exécuteur SQL : `query` pour l'aperçu (lecture seule), celui de `transaction()` pour la confirmation (tout ou rien).
// Rien n'est journalisé ici (aucune donnée de ligne dans les logs) ; le journal d'import ne garde que des empreintes et des issues.
import { IMPORT_MAX_BYTES } from "./csvimport-limits";
import {
  AnalyzeContext, AnalyzedRow, DbProspect, ImportRow, Resolution, analyze, checkUpload, countKinds, createValues, decide, fillPlan, importFingerprint,
  previewToken, readCsvFile, safeFilename,
} from "./csvimport";
import type { OptOut } from "./export";

export type Q = (sql: string, params?: any[]) => Promise<any>;

/** Erreur attendue (fichier refusé, aperçu périmé…) : message destiné à l'utilisateur, sans donnée de ligne. */
export class ImportError extends Error {
  constructor(readonly status: 400 | 409 | 413 | 415, message: string) { super(message); }
}

const CHUNK = 500;
async function chunked<T>(values: T[], fn: (chunk: T[]) => Promise<any[]>): Promise<any[]> {
  const out: any[] = [];
  for (let i = 0; i < values.length; i += CHUNK) out.push(...(await fn(values.slice(i, i + CHUNK))));
  return out;
}
const uniq = <T,>(xs: (T | null | undefined)[]) => [...new Set(xs.filter((x): x is T => x != null && x !== ""))];

const PROSPECT_COLS = `id, siren, siret, company_name, trade_name, activity_key, activity_label, address, city, postal_code, phone, email, website_url,
  website_status, contact_page, social_links, contacted_at, do_not_contact, status`;

/** Contexte du rapprochement. `lock` (confirmation) : les fiches concernées sont verrouillées jusqu'au COMMIT (FOR UPDATE). */
export async function loadContext(q: Q, rows: ImportRow[], fileHashHex: string, lock = false):
  Promise<AnalyzeContext & { previous: { id: number; created_at: string } | null }> {
  const sirens = uniq(rows.map((r) => r.data.siren));
  const sirets = uniq(rows.map((r) => r.data.siret));
  const emails = uniq(rows.map((r) => r.data.email));
  const hashes = uniq(rows.map((r) => r.hash));
  const forUpdate = lock ? " FOR UPDATE" : "";
  const seen = new Map<number, DbProspect>();
  for (const p of await chunked(sirens, (c) => q(`SELECT ${PROSPECT_COLS} FROM local_prospects WHERE siren IN (?) ORDER BY id${forUpdate}`, [c]))) seen.set(p.id, p);
  for (const p of await chunked(sirets, (c) => q(`SELECT ${PROSPECT_COLS} FROM local_prospects WHERE siret IN (?) ORDER BY id${forUpdate}`, [c]))) seen.set(p.id, p);
  const optOuts: OptOut[] = await q("SELECT siret, siren, email, domain FROM local_do_not_contact");
  const emailOwners = await chunked(emails, (c) => q("SELECT id, email FROM local_prospects WHERE email IN (?)", [c]));
  const appliedHashes: Record<string, number> = {};
  for (const h of await chunked(hashes, (c) => q(`SELECT r.row_hash, r.prospect_id FROM local_import_rows r JOIN local_prospects p ON p.id = r.prospect_id
      WHERE r.row_hash IN (?) AND r.outcome IN ('CREATED','UPDATED','UNCHANGED') ORDER BY r.id`, [c]))) appliedHashes[h.row_hash] = h.prospect_id;
  const [previous] = await q("SELECT id, created_at FROM local_imports WHERE file_hash=? ORDER BY id DESC LIMIT 1", [fileHashHex]);
  return { prospects: [...seen.values()], optOuts, emailOwners, appliedHashes, previous: previous ?? null };
}

/** Corps reçu du navigateur : { filename, mime, data (base64) } → octets contrôlés (extension, type, taille). */
export function decodeUpload(body: Record<string, any>): { bytes: Uint8Array; filename: string } {
  const data = body.data;
  if (typeof data !== "string" || !data) throw new ImportError(400, "Fichier manquant");
  if (data.length > Math.ceil(IMPORT_MAX_BYTES / 3) * 4 + 8) throw new ImportError(413, `Fichier trop volumineux (maximum ${IMPORT_MAX_BYTES / 1024 / 1024} Mo)`);
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(data)) throw new ImportError(400, "Fichier mal transmis");
  const bytes = new Uint8Array(Buffer.from(data, "base64"));
  const err = checkUpload({ filename: body.filename, mime: body.mime, size: bytes.length });
  if (err) throw new ImportError(/volumineux/.test(err) ? 413 : /Type|Extension/.test(err) ? 415 : 400, err);
  return { bytes, filename: safeFilename(body.filename) };
}

export interface PreviewResult {
  filename: string; fileHash: string; separator: string; bom: boolean; headerWarnings: string[]; hasSiret: boolean;
  previous: { id: number; created_at: string } | null; counts: ReturnType<typeof countKinds>; rows: AnalyzedRow[]; token: string;
}

/** APERÇU : lecture seule (aucune écriture, aucune transaction). */
export async function previewImport(q: Q, bytes: Uint8Array, filename: string, now = new Date()): Promise<PreviewResult> {
  const parsed = readCsvFile(bytes, now);
  if (!parsed.ok) throw new ImportError(400, parsed.error);
  const ctx = await loadContext(q, parsed.rows, parsed.fileHash);
  const rows = analyze(parsed.rows, ctx);
  return {
    filename, fileHash: parsed.fileHash, separator: parsed.separator, bom: parsed.bom, headerWarnings: parsed.header.warnings, hasSiret: parsed.header.hasSiret,
    previous: ctx.previous, counts: countKinds(rows), rows, token: previewToken(parsed.fileHash, rows),
  };
}

export interface ImportSummary { importId: number; created: number; updated: number; unchanged: number; ignored: number; errors: number }

/**
 * CONFIRMATION, à appeler DANS `transaction()` : refait l'analyse sur les fiches verrouillées, refuse si le résultat diffère de l'aperçu
 * (jeton), applique uniquement les lignes confirmées et écrit le journal. Toute erreur SQL remonte : la transaction est annulée en entier.
 */
export async function confirmImport(q: Q, input: { bytes: Uint8Array; filename: string; token: unknown; resolutions: Record<number, Resolution>; skip: Set<number> },
  now = new Date()): Promise<ImportSummary> {
  const parsed = readCsvFile(input.bytes, now);
  if (!parsed.ok) throw new ImportError(400, parsed.error);
  const ctx = await loadContext(q, parsed.rows, parsed.fileHash, true);
  const rows = analyze(parsed.rows, ctx);
  if (typeof input.token !== "string" || input.token !== previewToken(parsed.fileHash, rows))
    throw new ImportError(409, "Les fiches ont changé depuis l'aperçu : relance l'aperçu avant de confirmer.");
  const dec = decide(rows, input.resolutions, input.skip);
  if (!dec.ok) throw new ImportError(400, dec.error);

  const ins = await q("INSERT INTO local_imports (created_at, filename, file_hash, file_bytes, separator_char, total_rows) VALUES (UTC_TIMESTAMP(),?,?,?,?,?)",
    [input.filename, parsed.fileHash, input.bytes.length, parsed.separator, rows.length]);
  const importId = Number(ins.insertId);
  const nowIso = now.toISOString().slice(0, 19) + "Z";
  const byLine = new Map(parsed.rows.map((r) => [r.line, r]));
  const state = new Map(ctx.prospects.map((p) => [p.id, { ...p }]));
  const sirets = new Map(ctx.prospects.filter((p) => p.siret).map((p) => [p.siret!, p.id]));
  const creates = dec.decisions.filter((x) => x.op === "create").map((x) => byLine.get(x.line)!);
  const taken = new Set<string>((await chunked(creates.map((r) => importFingerprint(r.data)),
    (c) => q("SELECT fingerprint FROM local_prospects WHERE fingerprint IN (?)", [c]))).map((x) => x.fingerprint));

  const journal: unknown[][] = [];
  const counts = { CREATED: 0, UPDATED: 0, UNCHANGED: 0, IGNORED: 0, SKIPPED: 0, ERROR: 0 };
  const log = (row: ImportRow, outcome: keyof typeof counts, id: number | null, reason: string, fields = "") => {
    counts[outcome]++;
    journal.push([importId, row.line, row.hash, outcome, id, reason.slice(0, 255), fields.slice(0, 255) || null]);
  };
  const source = (pid: number, line: number) =>
    q("INSERT IGNORE INTO local_prospect_sources (prospect_id, source, source_ref) VALUES (?, 'csv_import', ?)", [pid, `import ${importId} ligne ${line}`]);

  for (const d of dec.decisions) {
    const row = byLine.get(d.line)!;
    if (d.op === "none") { log(row, d.outcome, d.id, d.reason); continue; }
    if (d.op === "create") {
      const values = createValues(row.data, importId, nowIso);
      if (taken.has(String(values.fingerprint)) || (row.data.siret && sirets.has(row.data.siret))) { log(row, "ERROR", null, "Fiche identique déjà créée"); continue; }
      const cols = Object.keys(values);
      const res = await q(`INSERT INTO local_prospects (${cols.join(", ")}) VALUES (?)`, [cols.map((c) => values[c])]);
      const pid = Number(res.insertId);
      await source(pid, row.line);
      taken.add(String(values.fingerprint));
      if (row.data.siret) sirets.set(row.data.siret, pid);
      log(row, "CREATED", pid, d.reason, "création");
      continue;
    }
    const p = state.get(d.id);
    if (!p) { log(row, "ERROR", null, "Fiche introuvable"); continue; }
    const siretTaken = !!row.data.siret && sirets.has(row.data.siret) && sirets.get(row.data.siret) !== p.id;
    const plan = fillPlan(p, row.data, siretTaken);
    const cols = Object.keys(plan.sets);
    if (!cols.length) { log(row, "UNCHANGED", p.id, d.reason); continue; }
    const prov = JSON.stringify({ source: "import CSV", import_id: importId, at: nowIso, fields: plan.fields });
    // Fiche verrouillée (FOR UPDATE) et plan calculé sur son état courant ; la garde d'opposition est revérifiée par la requête elle-même.
    await q(`UPDATE local_prospects SET ${cols.map((c) => `${c}=?`).join(", ")},
               provenance = JSON_SET(COALESCE(provenance, JSON_OBJECT()), '$.import_csv', CAST(? AS JSON))
             WHERE id=? AND do_not_contact=0 AND status <> 'DO_NOT_CONTACT'`, [...cols.map((c) => plan.sets[c]), prov, p.id]);
    for (const k of ["phone", "email"] as const) {
      if (k in plan.sets) await q(`UPDATE local_prospects SET provenance = JSON_INSERT(provenance, '$.${k}', CAST(? AS JSON)) WHERE id=?`, [JSON.stringify({ source: "import CSV" }), p.id]);
    }
    await source(p.id, row.line);
    Object.assign(p, plan.sets);
    if (plan.sets.siret) sirets.set(String(plan.sets.siret), p.id);
    log(row, "UPDATED", p.id, d.reason, plan.fields.join(", "));
  }
  for (let i = 0; i < journal.length; i += CHUNK) {
    await q("INSERT INTO local_import_rows (import_id, line_no, row_hash, outcome, prospect_id, reason, fields) VALUES ?", [journal.slice(i, i + CHUNK)]);
  }
  await q(`UPDATE local_imports SET created_count=?, updated_count=?, unchanged_count=?, ignored_count=?, error_count=? WHERE id=?`,
    [counts.CREATED, counts.UPDATED, counts.UNCHANGED, counts.IGNORED + counts.SKIPPED, counts.ERROR, importId]);
  return { importId, created: counts.CREATED, updated: counts.UPDATED, unchanged: counts.UNCHANGED, ignored: counts.IGNORED + counts.SKIPPED, errors: counts.ERROR };
}
