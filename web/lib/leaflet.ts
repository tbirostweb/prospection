// Chargement de Leaflet (carte) depuis cdnjs, au moment où la carte s'affiche : aucune dépendance npm à installer, intégrité vérifiée (SRI).
const VERSION = "1.9.4";
const JS = { src: `https://cdnjs.cloudflare.com/ajax/libs/leaflet/${VERSION}/leaflet.min.js`, integrity: "sha384-NElt3Op+9NBMCYaef5HxeJmU4Xeard/Lku8ek6hoPTvYkQPh3zLIrJP7KiRocsxO" };
const CSS = { href: `https://cdnjs.cloudflare.com/ajax/libs/leaflet/${VERSION}/leaflet.min.css`, integrity: "sha384-c6Rcwz4e4CITMbu/NBmnNS8yN2sC3cUElMEMfP3vqqKFp7GOYaaBBCqmaWBjmkjb" };
let pending: Promise<any> | null = null;

export function loadLeaflet(): Promise<any> {
  const w = window as any;
  if (w.L) return Promise.resolve(w.L);
  if (pending) return pending;
  pending = new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${CSS.href}"]`)) {
      const l = document.createElement("link");
      Object.assign(l, { rel: "stylesheet", href: CSS.href, integrity: CSS.integrity, crossOrigin: "anonymous" });
      document.head.appendChild(l);
    }
    const s = document.createElement("script");
    Object.assign(s, { src: JS.src, integrity: JS.integrity, crossOrigin: "anonymous", async: true });
    s.onload = () => (w.L ? resolve(w.L) : reject(new Error("Leaflet indisponible")));
    s.onerror = () => { pending = null; reject(new Error("Impossible de charger la carte (réseau ou CDN indisponible)")); };
    document.head.appendChild(s);
  });
  return pending;
}
