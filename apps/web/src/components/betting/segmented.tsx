"use client";

// A full-width segmented bar (owner, Nov 25): equal columns on the phone, its
// natural width on desktop, the chosen segment raised in the accent (Game Day's
// switch). Each segment shows how many items a tap would show. Shared by the
// Parlays and Props tabs.

const COLS: Record<number, string> = { 2: "grid-cols-2", 3: "grid-cols-3", 4: "grid-cols-4" };

export function Segmented<T extends string>({ label, options, value, counts, name, testid, onChange }: {
  label: string;
  options: readonly T[];
  value: T;
  counts?: Partial<Record<T, number>>;
  name: (o: T) => string;
  testid: string;
  onChange: (o: T) => void;
}) {
  return (
    <div role="group" aria-label={label}
         className={`grid w-full ${COLS[options.length] ?? "grid-cols-4"} gap-1 rounded-pill border border-border bg-surface-2 p-1 lg:w-fit`}>
      {options.map((o) => {
        const on = o === value;
        return (
          <button key={o} type="button" aria-pressed={on} data-testid={`${testid}-${o}`} onClick={() => onChange(o)}
                  className={`inline-flex min-h-10 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-pill border px-1.5 text-sm font-semibold transition-colors lg:px-5 ${
                    on ? "border-accent bg-accent text-on-accent" : "border-transparent text-text-muted hover:text-text"}`}>
            {name(o)}
            {counts && (
              <span data-testid={`${testid}-${o}-n`} className="font-mono text-xs tabular-nums">{counts[o] ?? 0}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
