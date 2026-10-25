import type { Config } from "tailwindcss";

// Toutes les couleurs viennent des variables CSS (tokens) définies dans app/globals.css :
// une seule source de vérité, et les modificateurs d'opacité Tailwind (bg-paper/40, text-ink/70…) restent utilisables.
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;
const night = { DEFAULT: token("night"), bg: token("night"), text: token("night-text"), dim: token("night-dim"), line: token("night-line") };

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: token("canvas"),
        surface: token("surface"),
        paper: token("paper"),
        ink: token("ink"),
        signal: token("signal"),
        accent: { DEFAULT: token("accent"), dark: token("accent-dark"), light: token("accent-light") },
        muted: token("muted"),
        line: token("line"),
        line2: token("line2"),
        night,
        dark: night,
      },
      boxShadow: { soft: "var(--shadow-soft)" },
      fontFamily: {
        // Police unique : Inter, pour les titres, le texte et les chiffres (tabulaires via `tabular-nums`).
        display: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
        sans: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
      },
      maxWidth: { content: "1160px" },
    },
  },
  plugins: [],
} satisfies Config;
