"use client";

import { useEffect } from "react";

import { useRouter } from "next/navigation";

// Re-renders the page from the server every `seconds` while it is mounted
// (the parent mounts it only while something is still being written or
// checked), and stops after `forMinutes` so a stuck job never polls forever.
// Paused while the tab is hidden.

export function RefreshWhile({ seconds, forMinutes = 10 }: { seconds: number; forMinutes?: number }) {
  const router = useRouter();
  useEffect(() => {
    const until = Date.now() + forMinutes * 60_000;
    const id = window.setInterval(() => {
      if (Date.now() > until) window.clearInterval(id);
      else if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => window.clearInterval(id);
  }, [router, seconds, forMinutes]);
  return null;
}
