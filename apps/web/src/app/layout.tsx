import type { Metadata, Viewport } from "next";
import { cookies, headers } from "next/headers";

import { BOOT_GATE_SCRIPT, BootSplash } from "@/components/boot-splash";
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
  // Set by src/proxy.ts with the CSP; without it the script is not rendered,
  // and the splash still fades itself out.
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    <html
      lang="en"
      data-theme={theme}
      className={`${display.variable} ${ui.variable} ${mono.variable}`}
      // The boot gate may add data-booted before hydration.
      suppressHydrationWarning
    >
      <head>
        {nonce && (
          <script
            nonce={nonce}
            suppressHydrationWarning
            dangerouslySetInnerHTML={{ __html: BOOT_GATE_SCRIPT }}
          />
        )}
      </head>
      <body className="min-h-dvh bg-bg text-text">
        <BootSplash />
        {children}
      </body>
    </html>
  );
}
