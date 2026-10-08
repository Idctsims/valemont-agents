"use client";

import { SerwistProvider } from "@serwist/turbopack/react";

/**
 * Registers /serwist/sw.js (scope "/"). Off in `next dev`, where a worker
 * holding stale bundles only gets in the way; Playwright exercises it against
 * a production build.
 *
 * cacheOnNavigation is OFF on purpose: the provider's default messages the
 * worker to cache every page visited, which would store signed-in HTML.
 */
export function ServiceWorker({ children }: { children: React.ReactNode }) {
  return (
    <SerwistProvider
      swUrl="/serwist/sw.js"
      disable={process.env.NODE_ENV === "development"}
      cacheOnNavigation={false}
      options={{ scope: "/", type: "classic" }}
    >
      {children}
    </SerwistProvider>
  );
}
