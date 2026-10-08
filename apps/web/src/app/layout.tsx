import type { Metadata, Viewport } from "next";
import { cookies, headers } from "next/headers";

import { BOOT_GATE_SCRIPT, BootSplash } from "@/components/boot-splash";
import { ServiceWorker } from "@/components/pwa/service-worker";
import { THEME_COLORS } from "@/generated/theme-colors";
import { THEME_COOKIE, parseTheme } from "@/lib/theme";

import { display, mono, ui } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Valemont Command", template: "%s · Valemont" },
  description: "Private command center.",
  // Everything is private except /privacy, which overrides this.
  robots: { index: false, follow: false },
  // Installed to the iPhone Home Screen: full screen, content under a
  // translucent status bar (the shell pads with safe-area insets).
  appleWebApp: { capable: true, title: "Valemont", statusBarStyle: "black-translucent" },
  // Next emits mobile-web-app-capable for the line above; iOS still reads the
  // apple- prefixed name, so it is stated explicitly.
  other: { "apple-mobile-web-app-capable": "yes" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: THEME_COLORS.night.bg,
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
        <ServiceWorker>{children}</ServiceWorker>
      </body>
    </html>
  );
}
