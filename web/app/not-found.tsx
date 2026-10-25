import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
      <div className="big-number text-[clamp(4rem,15vw,7rem)] text-ink/20">404</div>
      <p className="mono">Cette page n’existe pas</p>
      <Link href="/" className="btn-primary">Retour à l'accueil</Link>
    </div>
  );
}
