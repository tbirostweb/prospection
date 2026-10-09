import type { Config } from "tailwindcss";

// Toutes les couleurs viennent des variables CSS (tokens) définies dans app/globals.css :
// une seule source de vérité, et les modificateurs d'opacité Tailwind (bg-paper/40, text-ink/70…) restent utilisables.
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: token("surface"),
        paper: token("paper"),
        ink: token("ink"),
        accent: { DEFAULT: token("accent"), dark: token("accent-dark"), light: token("accent-light") },
        muted: token("muted"),
        line: token("line"),
        line2: token("line2"),
        // Bandeau bleu nuit (menu, pied de page)
        dark: { bg: token("night"), text: token("night-text"), dim: token("night-dim"), line: token("night-line") },
      },
      fontFamily: {
        display: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
        sans: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
        // Chiffres : même grotesque, en chiffres tabulaires (voir .font-mono dans globals.css)
        mono: ["var(--font-sans)", "Helvetica Neue", "Arial", "sans-serif"],
      },
      borderRadius: { DEFAULT: "4px" },
      maxWidth: { content: "1160px" },
    },
  },
  plugins: [],
} satisfies Config;
