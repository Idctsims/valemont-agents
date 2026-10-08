import { signature } from "@/app/fonts";

/** The splash/boot screen: one of two places the pixel signature face appears. */
export function BootScreen() {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={`${signature.variable} flex min-h-dvh flex-col items-center justify-center gap-4 bg-bg px-safe`}
    >
      <p className="font-signature text-2xl text-accent">VALEMONT</p>
      <p className="label-mono flex items-center gap-2 text-text-muted">
        Opening command
        <span aria-hidden className="inline-block h-3 w-2 animate-pulse bg-accent-strong" />
      </p>
    </div>
  );
}
