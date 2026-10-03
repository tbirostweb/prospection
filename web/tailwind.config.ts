import type { Config } from "tailwindcss";

// Toutes les couleurs viennent des variables CSS (tokens) définies dans app/globals.css :
// une seule source de vérité, et les modificateurs d'opacité Tailwind (bg-paper/40, text-ink/70…) restent utilisables.
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;
const night = { DEFAULT: token("night"), bg: token("night"), text: token("night-text"), dim: token("night-dim"), line: token("night-line") };
// Néo-brutalisme : aucun arrondi, nulle part (y compris rounded-full).
const square = Object.fromEntries(["none", "sm", "DEFAULT", "md", "lg", "xl", "2xl", "3xl", "full"].map((k) => [k, "0"]));

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    borderRadius: square,
    // Pas d'ombre floue : seulement une ombre dure décalée.
    boxShadow: { none: "none", DEFAULT: "var(--shadow-hard)", hard: "var(--shadow-hard)" },
    extend: {
      colors: {
        surface: token("surface"),
        paper: token("paper"),
        ink: token("ink"),
        signal: token("signal"),
        accent: { DEFAULT: token("accent"), dark: token("accent-dark"), light: token("accent-light") },
        muted: token("muted"),
        line: token("line"),
        line2: token("line2"),
        // Blocs sombres (pied de page, chiffres) ; « dark » gardé comme alias.
        night,
        dark: night,
      },
      fontFamily: {
        // Titres : IBM Plex Sans Condensed (police de titres de birostweb.fr)
        display: ["var(--font-display)", "Arial Narrow", "Helvetica Neue", "sans-serif"],
        // Texte courant : IBM Plex Sans
        sans: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
        // Labels, navigation, boutons, chiffres : IBM Plex Mono
        mono: ["var(--font-mono)", "ui-monospace", "Menlo", "monospace"],
      },
      maxWidth: { content: "1160px" },
    },
  },
  plugins: [],
} satisfies Config;
