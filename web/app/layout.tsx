import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import AppShell from "@/components/AppShell";

const sans = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800", "900"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Prospection locale",
  description: "Prospection locale : savoir si une entreprise a un site et comment la contacter",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#101B33" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" className={sans.variable}>
      <body>
        {/* AppShell : barre de navigation + pied de page, sauf sur /login (plein écran). */}
        <AppShell
          footer={
            <footer className="bg-dark-bg px-4 py-6 text-dark-dim sm:px-6 md:px-10">
              <div className="mx-auto flex max-w-content flex-wrap items-baseline justify-between gap-x-6 gap-y-2 text-xs">
                <span className="font-display text-sm font-black uppercase tracking-[-0.02em] text-dark-text">Prospection locale</span>
                <span>Rien n&apos;est jamais envoyé automatiquement : tu contactes toi-même.</span>
                <span className="mono !text-dark-dim">Campagnes · toutes les 15 min</span>
              </div>
            </footer>
          }
        >
          {children}
        </AppShell>
      </body>
    </html>
  );
}
