"use client";

import { DotsThree } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";

// Small pieces the venture detail is built from: tap-to-edit text, a row
// menu, and a chip group. Tokens only; every control is a 44 px target.

/**
 * Text that edits in place. Tap the text (or its placeholder) to edit; Enter
 * saves a single line, Cmd/Ctrl+Enter saves a block, Escape cancels.
 */
export function InlineText({
  value,
  placeholder,
  label,
  onSave,
  multiline = false,
  maxLength,
  className = "",
  testId,
}: {
  value: string | null;
  placeholder: string;
  label: string;
  onSave: (next: string | null) => void;
  multiline?: boolean;
  maxLength: number;
  className?: string;
  testId?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const field = useRef<HTMLInputElement & HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing) field.current?.focus();
  }, [editing]);

  const save = () => {
    setEditing(false);
    const next = draft.trim() || null;
    if (next !== (value ?? null)) onSave(next);
  };

  if (!editing) {
    return (
      <button
        type="button"
        data-testid={testId}
        aria-label={`${label}: ${value || "not set"}. Tap to edit`}
        onClick={() => {
          setDraft(value ?? "");
          setEditing(true);
        }}
        className={`tap block w-full text-left text-pretty whitespace-pre-line transition-colors hover:text-accent ${
          value ? "" : "text-text-muted"
        } ${className}`}
      >
        {value || placeholder}
      </button>
    );
  }

  const shared = {
    ref: field,
    value: draft,
    maxLength,
    "aria-label": label,
    onChange: (e: React.ChangeEvent<HTMLInputElement & HTMLTextAreaElement>) => setDraft(e.target.value),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Escape") setEditing(false);
      if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        save();
      }
    },
  };

  return (
    <div>
      {multiline ? (
        <textarea
          {...shared}
          rows={Math.min(12, Math.max(3, draft.split("\n").length + 1))}
          className={`w-full resize-y border-b border-accent bg-transparent pb-1 text-text outline-none ${className}`}
        />
      ) : (
        <input {...shared} className={`w-full border-b border-accent bg-transparent pb-1 text-text outline-none ${className}`} />
      )}
      <div className="mt-1 flex gap-2">
        <button type="button" onClick={save} className="tap rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2">
          Save
        </button>
        <button
          type="button"
          onClick={() => setEditing(false)}
          className="tap rounded-pill px-3 text-sm text-text-muted hover:bg-surface-2"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** A ⋯ button with a small menu of actions. */
export function MoreMenu({
  label,
  items,
}: {
  label: string;
  items: { label: string; onSelect: () => void; danger?: boolean }[];
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={`tap inline-flex items-center justify-center rounded-pill transition-colors hover:bg-surface-2 hover:text-text ${
          open ? "bg-surface-2 text-text" : "text-text-muted"
        }`}
      >
        <DotsThree size={22} weight="bold" aria-hidden />
      </button>
      {open && (
        <div id={id} role="menu" className="absolute top-full right-0 z-20 mt-1 min-w-44 rounded-inner border border-border bg-surface p-1">
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                it.onSelect();
              }}
              className={`tap flex w-full items-center rounded-inner px-3 text-left text-sm transition-colors hover:bg-surface-2 ${
                it.danger ? "text-danger" : "text-text"
              }`}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A single-choice row of mono chips. */
export function Chips<T extends string>({
  label,
  options,
  value,
  onChange,
  render = (v) => v,
  allowNone = false,
}: {
  label: string;
  options: readonly T[];
  value: T | null;
  onChange: (v: T | null) => void;
  render?: (v: T) => string;
  allowNone?: boolean;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          aria-pressed={value === o}
          onClick={() => onChange(value === o && allowNone ? null : o)}
          className={`tap inline-flex items-center rounded-pill border px-3.5 font-mono text-xs transition-colors ${
            value === o
              ? "border-accent bg-accent text-on-accent"
              : "border-border text-text-muted hover:bg-surface-2 hover:text-text"
          }`}
        >
          {render(o)}
        </button>
      ))}
    </div>
  );
}

/** A section heading: the small mono label the ledger uses. */
export function SectionLabel({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-4">
      <h2 className="label-mono text-text-muted">{children}</h2>
      {aside && <span className="font-mono text-xs text-text-muted">{aside}</span>}
    </div>
  );
}
