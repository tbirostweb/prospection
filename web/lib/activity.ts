// Métier affiché dans un message ou envoyé à la file /rediger : forme COURTE du libellé d'activité (jamais un intitulé NAF entier).
// Les libellés du worker (worker/local/data/naf_rev2.json) sont déjà abrégés ; ceci protège des libellés libres (import CSV, réglages).
const MAX = 40;

/** 1re partie avant la virgule ; au-delà de 40 caractères, coupée avant « et » / « ou » / « ; » / « ( », sinon aux mots entiers. null si vide. */
export function shortActivity(label: string | null | undefined): string | null {
  let s = String(label ?? "").split(",")[0].trim();
  if (!s) return null;
  if (s.length > MAX) {
    const cut = s.split(/\s+(?:et|ou)\s+|\s*[;(]/)[0].trim();
    s = cut.length >= 4 && cut.length <= MAX ? cut : s.slice(0, MAX).replace(/\s+\S*$/, "").trim();
  }
  return s || null;
}

/** « des plombiers », « isolation » : ce que le client tape sur Google (un pluriel prend « des », un nom d'activité reste tel quel). */
export function searchedAs(label: string | null | undefined): string | null {
  const a = shortActivity(label)?.toLowerCase();
  return a ? (/[sx]$/.test(a) ? `des ${a}` : a) : null;
}
