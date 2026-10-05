/** @type {import('next').NextConfig} */
// En-têtes de sécurité : source unique dans lib/security.ts (testée par `npm test`), recopiée ici en JS
// car next.config.js est chargé sans compilation TypeScript. Garder les deux listes identiques (test de cohérence dans tests/).
const dev = process.env.NODE_ENV !== "production";
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
  "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://cdnjs.cloudflare.com",
  "font-src 'self' data:",
  `connect-src 'self' https://geo.api.gouv.fr${dev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");
const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "X-Robots-Tag", value: "noindex, nofollow" },
  ...(dev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};
module.exports = nextConfig;
