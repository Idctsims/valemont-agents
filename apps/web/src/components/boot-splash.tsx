import { signature } from "@/app/fonts";

/**
 * Splash for a cold start of the installed PWA: one of two places the pixel
 * signature face appears (the other is the 404).
 *
 * Pure CSS (tokens.css, .boot-splash): hidden unless display-mode is
 * standalone, fades itself out by 600ms, and pointer-events: none, so it
 * never blocks a tap or a navigation. Client-side navigations never re-render
 * the root layout, so it cannot reappear mid-session.
 */
export function BootSplash() {
  return (
    <div
      aria-hidden
      className={`boot-splash ${signature.variable} flex-col items-center justify-center gap-4 bg-bg`}
    >
      <p className="font-signature text-2xl text-accent">VALEMONT</p>
      <p className="label-mono flex items-center gap-2 text-text-muted">
        Opening command
        <span className="inline-block h-3 w-2 animate-pulse bg-accent-strong" />
      </p>
    </div>
  );
}

/**
 * Runs before first paint. Marks <html data-booted> if this app session has
 * already shown the splash (sessionStorage is fresh on every cold start), so
 * a reload inside the running app skips it.
 */
export const BOOT_GATE_SCRIPT =
  "try{var s=sessionStorage;if(s.getItem('vm-booted'))document.documentElement.setAttribute('data-booted','');else s.setItem('vm-booted','1')}catch(e){}";
