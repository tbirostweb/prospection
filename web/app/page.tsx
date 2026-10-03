import { redirect } from "next/navigation";

// La page d'accueil est la liste « À contacter » : l'écran de travail principal.
export default function Home() {
  redirect("/local/contact");
}
