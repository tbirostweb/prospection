import "./globals.css";
import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, IBM_Plex_Sans_Condensed } from "next/font/google";
import Sidebar from "@/components/Sidebar";

// Polices de birostweb.fr : titres en Plex Sans Condensed, texte en Plex Sans, labels en Plex Mono.
const display = IBM_Plex_Sans_Condensed({ subsets: ["latin"], weight: ["600", "700"], variable: "--font-display", display: "swap" });
const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-sans", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Prospection locale",
  description: "Prospection locale : savoir si une entreprise a un site et comment la contacter",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#FBFAF6" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body>
        <div className="flex min-h-screen flex-col">
          <Sidebar />
          <main className="min-w-0 flex-1 px-4 py-8 sm:px-6 md:px-10 md:py-14">
            <div className="mx-auto w-full max-w-content">{children}</div>
          </main>
          <footer className="bg-night text-night-text">
            <div className="mx-auto grid max-w-content gap-6 px-4 py-10 sm:px-6 md:grid-cols-[1fr_auto] md:items-end md:px-10 md:py-14">
              <div className="min-w-0">
                <div className="font-display font-bold uppercase leading-[0.9]" style={{ fontSize: "clamp(2rem, 6vw + 0.5rem, 4.5rem)" }}>
                  Prospection <span className="text-signal">locale</span>
                </div>
                <p className="mt-4 max-w-xl font-mono text-xs uppercase leading-relaxed tracking-wide text-night-dim">
                  Rien n&apos;est jamais envoyé automatiquement : tu contactes toi-même.
                </p>
              </div>
              <div className="flex flex-col gap-1 border-t-2 border-night-line pt-4 font-mono text-xs uppercase tracking-wide text-night-dim md:border-l-2 md:border-t-0 md:pl-6 md:pt-0">
                <span><span className="text-accent-light">●</span> Campagnes · toutes les 15 min</span>
                <span>Données publiques · SIRENE · OSM</span>
              </div>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
