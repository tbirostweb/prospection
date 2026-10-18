// Limites de l'import CSV, partagées par le module pur (lib/csvimport.ts), les routes et l'écran (aucune dépendance Node : importable côté client).
export const IMPORT_MAX_BYTES = 2 * 1024 * 1024;          // 2 Mo
export const IMPORT_MAX_ROWS = 5000;                      // lignes de données (hors en-tête)
/** Corps JSON des routes d'import : fichier en base64 (+33 %) et résolutions des lignes. */
export const IMPORT_MAX_BODY = 3_600_000;
export const IMPORT_MIME_TYPES = ["", "text/csv", "text/plain", "application/csv", "text/x-csv", "application/x-csv", "application/vnd.ms-excel", "text/comma-separated-values"];
