import { NextRequest, NextResponse } from "next/server";
import { MAX_JSON_BYTES, parseId, parseJsonBody } from "./security";

/** Corps JSON borné (64 Ko) ; renvoie soit l'objet, soit la réponse d'erreur 400/413 à retourner telle quelle. */
export async function readJson(req: NextRequest): Promise<{ body: Record<string, any> } | { error: NextResponse }> {
  const declared = Number(req.headers.get("content-length") ?? 0);
  if (declared > MAX_JSON_BYTES) return { error: NextResponse.json({ error: "corps de requête trop volumineux" }, { status: 413 }) };
  let text: string;
  try { text = await req.text(); } catch { return { error: NextResponse.json({ error: "corps illisible" }, { status: 400 }) }; }
  const r = parseJsonBody(text);
  if (!r.ok) return { error: NextResponse.json({ error: r.error }, { status: r.status }) };
  return { body: r.value };
}

/** Identifiant de route entier positif ; sinon réponse 400. */
export async function routeId(params: Promise<{ id: string }>): Promise<{ id: number } | { error: NextResponse }> {
  const { id } = await params;
  const n = parseId(id);
  return n === null ? { error: NextResponse.json({ error: "identifiant invalide" }, { status: 400 }) } : { id: n };
}
