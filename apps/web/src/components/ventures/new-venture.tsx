"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { createVenture } from "@/app/(app)/ventures/actions";

/** "+ Venture": a quiet text action that becomes a name field. */
export function NewVenture() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, startTransition] = useTransition();

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="tap mt-4 inline-flex items-center rounded-pill px-3 -ml-3 text-sm text-accent transition-colors hover:bg-surface-2"
      >
        + Venture
      </button>
    );
  }

  return (
    <form
      className="mt-4"
      aria-busy={saving}
      onSubmit={(e) => {
        e.preventDefault();
        const clean = name.trim();
        if (!clean) return;
        startTransition(async () => {
          try {
            const r = await createVenture(clean);
            if (r.ok) router.push(`/ventures/${r.slug}?edit=1`);
            else setError(r.error);
          } catch {
            // Only the network can throw here (src/lib/action-result.ts).
            setError("That didn't save. Check the connection and try again.");
          }
        });
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
    >
      <div className="flex items-center gap-2 border-b border-accent">
        <input
          autoFocus
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          placeholder="Venture name"
          aria-label="Venture name"
          className="tap min-w-0 flex-1 bg-transparent text-base text-text outline-none placeholder:text-text-muted"
        />
        <button type="submit" disabled={saving} className="tap shrink-0 rounded-pill px-3 text-sm font-medium text-accent hover:bg-surface-2 disabled:opacity-55">
          {saving ? "Adding" : "Add"}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  );
}
