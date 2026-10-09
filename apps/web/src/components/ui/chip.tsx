/**
 * What a chip looks like: a 32 px outlined pill drawn inside its button's
 * 44 px hit area (the button carries `tap group`). Selected is the accent
 * outline and text, not a solid fill: a filled pill read as a button and
 * outshouted the page (Chat 2 Phase 2 screenshot review).
 */
export function ChipFace({
  selected,
  mono = false,
  children,
}: {
  selected: boolean;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`inline-flex h-8 items-center rounded-pill border px-3 text-xs transition-colors ${mono ? "font-mono" : ""} ${
        selected
          ? "border-accent bg-surface-2 text-accent"
          : "border-border text-text-muted group-hover:bg-surface-2 group-hover:text-text"
      }`}
    >
      {children}
    </span>
  );
}
