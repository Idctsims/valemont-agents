import type { Metadata } from "next";

export const metadata: Metadata = { title: "Offline" };

// Public on purpose: the service worker (Phase 3) serves this when the
// network is gone, and it must render without a session.
export default function OfflinePage() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-center px-safe pt-safe pb-safe">
      <p className="label-mono text-text-muted">No connection</p>
      <h1 className="mt-3 font-display text-4xl text-text">You&apos;re offline.</h1>
      <p className="mt-4 text-lg text-text-muted text-pretty">
        Everything lives on the server, so nothing loads until the connection is back. Reopen the
        app once you&apos;re online.
      </p>
    </main>
  );
}
