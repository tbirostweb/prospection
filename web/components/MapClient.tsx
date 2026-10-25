"use client";
import dynamic from "next/dynamic";

// Leaflet a besoin du navigateur (window) : jamais rendu côté serveur.
const MapView = dynamic(() => import("./MapView"), { ssr: false, loading: () => <div className="h-[62vh] animate-pulse rounded-[var(--radius-card)] bg-surface bg-paper/40" /> });
export default MapView;
