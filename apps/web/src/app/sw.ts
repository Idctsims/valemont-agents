/// <reference lib="esnext" />
/// <reference lib="webworker" />
//
// The service worker, bundled by @serwist/turbopack (src/app/serwist/[path]).
//
// Single-user private data: the ONLY things this worker ever caches are
//   - the precache: Next's hashed /_next/static build output, public/ files
//     (icons) and /offline, a public page with no user data;
//   - same-origin GETs under /_next/static/ and /icons/ at runtime.
// Navigations go to the network every time (NetworkOnly) and fall back to the
// precached /offline when it is unreachable, so authenticated HTML is never
// stored. API routes, RSC payloads, Supabase calls (cross-origin) and anything
// carrying an Authorization header match no caching route at all.
import type { PrecacheEntry, RouteMatchCallbackOptions, SerwistGlobalConfig } from "serwist";
import { CacheFirst, ExpirationPlugin, NetworkOnly, Serwist } from "serwist";

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const OFFLINE = "/offline";

const isCacheableStatic = ({ request, url, sameOrigin }: RouteMatchCallbackOptions) =>
  sameOrigin &&
  request.method === "GET" &&
  !request.headers.has("authorization") &&
  (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/"));

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  skipWaiting: true,
  clientsClaim: true,
  // Preload would hand the network response to a route that never caches it
  // anyway; off keeps navigations a single plain fetch.
  navigationPreload: false,
  runtimeCaching: [
    {
      matcher: isCacheableStatic,
      handler: new CacheFirst({
        cacheName: "static",
        plugins: [new ExpirationPlugin({ maxEntries: 300, maxAgeSeconds: 60 * 60 * 24 * 30 })],
      }),
    },
    {
      matcher: ({ request }) => request.mode === "navigate",
      handler: new NetworkOnly(),
    },
  ],
  fallbacks: {
    entries: [{ url: OFFLINE, matcher: ({ request }) => request.destination === "document" }],
  },
});

serwist.addEventListeners();

// ---------------------------------------------------------------- push

type PushPayload = { title?: string; body?: string; url?: string; tag?: string };

/** Same-origin absolute paths only; anything else opens the app root. */
function safePath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return "/";
  }
  const url = new URL(value, self.location.origin);
  return url.origin === self.location.origin ? url.pathname + url.search + url.hash : "/";
}

self.addEventListener("push", (event) => {
  let data: PushPayload = {};
  try {
    data = (event.data?.json() as PushPayload) ?? {};
  } catch {
    data = { body: event.data?.text() };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Valemont", {
      body: data.body ?? "",
      icon: "/icons/icon-192.png",
      badge: "/icons/badge-96.png",
      tag: data.tag,
      data: { url: safePath(data.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = safePath((event.notification.data as { url?: unknown } | null)?.url);
  const target = new URL(path, self.location.origin).href;

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const existing = windows.find((c) => new URL(c.url).origin === self.location.origin);
      if (existing) {
        const focused = await existing.focus();
        if (focused.url !== target) await focused.navigate(target).catch(() => undefined);
        return;
      }
      await self.clients.openWindow(target);
    })(),
  );
});
