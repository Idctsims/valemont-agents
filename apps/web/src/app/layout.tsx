import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";

import { THEME_COOKIE, parseTheme } from "@/lib/theme";

import { display, mono, ui } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Valemont Command", template: "%s · Valemont" },
  description: "Private command center.",
  // Everything is private except /privacy, which overrides this.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Applied on the server, so the first paint is already in the right theme.
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);

  return (
    <html
      lang="en"
      data-theme={theme}
      className={`${display.variable} ${ui.variable} ${mono.variable}`}
    >
      <body className="min-h-dvh bg-bg text-text">{children}</body>
    </html>
  );
}
