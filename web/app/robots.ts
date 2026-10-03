import type { MetadataRoute } from "next";

// Outil privé : jamais indexé par les moteurs de recherche.
export default function robots(): MetadataRoute.Robots {
  return { rules: { userAgent: "*", disallow: "/" } };
}
