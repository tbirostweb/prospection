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

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#F2F1EE" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" className={sans.variable}>
      <body>
        {/* AppShell : barre de navigation + pied de page, sauf sur /login (plein écran). */}
        <AppShell
          footer={
            <footer className="px-4 pb-8 sm:px-6 md:px-10">
              <div className="mx-auto flex max-w-content flex-wrap justify-between gap-x-6 gap-y-1 text-xs text-muted">
                <span>Rien n&apos;est jamais envoyé automatiquement : tu contactes toi-même.</span>
                <span>Campagnes mises à jour toutes les 15 min</span>
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
