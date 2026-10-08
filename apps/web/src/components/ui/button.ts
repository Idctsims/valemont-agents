// Button and pill styles as class strings, so a <button>, a <Link> and a form
// submit share one look without a component kit.

const base =
  "tap inline-flex items-center justify-center gap-2 rounded-pill px-5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55";

export const button = {
  primary: `${base} bg-accent text-on-accent hover:bg-accent/90`,
  strong: `${base} bg-accent-strong text-on-accent-strong hover:bg-accent-strong/90`,
  secondary: `${base} border border-border bg-surface-2 text-text hover:bg-surface-3`,
  ghost: `${base} text-text-muted hover:bg-surface-2 hover:text-text`,
} as const;

export function chip(selected: boolean) {
  return `tap inline-flex items-center rounded-pill border px-4 text-sm transition-colors ${
    selected
      ? "border-accent bg-accent text-on-accent"
      : "border-border bg-transparent text-text-muted hover:bg-surface-2 hover:text-text"
  }`;
}
