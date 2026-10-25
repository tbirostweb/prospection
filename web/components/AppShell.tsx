"use client";
import { usePathname } from "next/navigation";
import Sidebar from "@/components/Sidebar";

/** Habillage commun (barre du haut, contenu, pied de page) ; la page de connexion est affichée seule, en plein écran. */
export default function AppShell({ children, footer }: { children: React.ReactNode; footer: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname === "/login") return <>{children}</>;
  return (
    <div className="flex min-h-screen flex-col">
      <Sidebar />
      <main className="min-w-0 flex-1 px-4 pb-12 pt-4 sm:px-6 md:px-10 md:pt-8">
        <div className="mx-auto w-full max-w-content">{children}</div>
      </main>
      {footer}
    </div>
  );
}
