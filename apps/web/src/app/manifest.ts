import type { MetadataRoute } from "next";

import { THEME_COLORS } from "@/generated/theme-colors";

// Served at /manifest.webmanifest. Colours come from tokens.css via the
// build-time generator, never typed here.
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "Valemont Command",
    short_name: "Valemont",
    description: "Private command center.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: THEME_COLORS.night.bg,
    theme_color: THEME_COLORS.night.bg,
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
