import { Geist, Geist_Mono, Instrument_Serif, Silkscreen } from "next/font/google";

// Self-hosted at build time by next/font: no request to Google at runtime,
// which is what lets the CSP keep font-src at 'self'.

export const display = Instrument_Serif({
  weight: "400",
  style: ["normal", "italic"],
  subsets: ["latin"],
  variable: "--font-instrument-serif",
});

export const ui = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
});

export const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

// Signature pixel face: the boot screen and the 404 only. It is applied by
// those components alone, so no other page preloads it.
export const signature = Silkscreen({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-silkscreen",
  preload: false,
});
