"use client";

/**
 * Prints the computed value of a --vm-* token in its own theme scope. The
 * page reads the live CSS, so the showcase can never drift from tokens.css.
 */
export function TokenValue({ name }: { name: string }) {
  return (
    <span
      className="label-mono text-text-muted"
      ref={(el) => {
        if (el) el.textContent = getComputedStyle(el).getPropertyValue(`--vm-${name}`).trim();
      }}
    />
  );
}
