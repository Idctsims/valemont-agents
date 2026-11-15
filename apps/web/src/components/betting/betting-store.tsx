"use client";

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";

import { loadLegs, saveLegs, toggleLeg, type ComboLeg } from "@/lib/betting/combo";
import { UNIT_KEY, parseUnit, type PriceUnit } from "@/lib/betting/price";

// The two choices /betting keeps in this browser (per-viewer conveniences, so
// localStorage; CLAUDE.md's artifact rule does not apply to the app): the
// slip's legs (owner, Nov 11: they persist across games and sports; the key
// the Phase 3 page used, so a slip carries over) and the x / % choice
// (Workstream B: "the choice sticks"). Read through useSyncExternalStore so
// the server render (empty slip, x) matches; blocked storage keeps them for
// this page only.

const LEGS_KEY = "vm-combo-legs";
const listeners = new Set<() => void>();
const fallback: Record<string, string | null> = {};

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("storage", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", fn);
  };
}

function read(key: string): string | null {
  try {
    const s = storage();
    return s ? s.getItem(key) : (fallback[key] ?? null);
  } catch {
    return fallback[key] ?? null;
  }
}

function write(key: string, value: string | null) {
  fallback[key] = value;
  try {
    const s = storage();
    if (s && value === null) s.removeItem(key);
    else if (s && value !== null) s.setItem(key, value);
  } catch {
    // storage blocked: the fallback keeps it for this page
  }
  for (const fn of listeners) fn();
}

type Store = {
  unit: PriceUnit;
  setUnit: (u: PriceUnit) => void;
  legs: readonly ComboLeg[];
  tick: (leg: ComboLeg) => void;
  remove: (ticker: string) => void;
  clear: () => void;
};

const Ctx = createContext<Store | null>(null);

export function BettingStore({ children }: { children: React.ReactNode }) {
  const rawUnit = useSyncExternalStore(subscribe, () => read(UNIT_KEY), () => null);
  const rawLegs = useSyncExternalStore(subscribe, () => read(LEGS_KEY), () => null);
  const unit = parseUnit(rawUnit);
  const legs = useMemo(() => loadLegs({ getItem: () => rawLegs }), [rawLegs]);

  const setLegs = useCallback((next: ComboLeg[]) => {
    const box = { setItem: (_k: string, v: string) => write(LEGS_KEY, v), removeItem: () => write(LEGS_KEY, null) };
    saveLegs(box, next);
  }, []);

  const store = useMemo<Store>(() => ({
    unit,
    setUnit: (u) => write(UNIT_KEY, u),
    legs,
    tick: (leg) => setLegs(toggleLeg(legs, leg)),
    remove: (t) => setLegs(legs.filter((l) => l.t !== t)),
    clear: () => setLegs([]),
  }), [unit, legs, setLegs]);

  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

export function useBetting(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useBetting outside BettingStore");
  return s;
}

/** The x / % switch: multiplier by default; the choice sticks in this browser. */
export function UnitSwitch() {
  const { unit, setUnit } = useBetting();
  const opts: { u: PriceUnit; face: string; label: string }[] = [
    { u: "x", face: "×", label: "Show prices as what $1 pays" },
    { u: "pct", face: "%", label: "Show prices as the chance they imply" },
  ];
  return (
    <div role="group" aria-label="Show prices as" data-testid="price-unit" data-unit={unit}
         className="inline-flex rounded-inner border border-border p-0.5">
      {opts.map((o) => (
        <button key={o.u} type="button" data-testid={`unit-${o.u}`} aria-pressed={unit === o.u} aria-label={o.label}
                onClick={() => setUnit(o.u)}
                className={`tap inline-flex items-center justify-center rounded-inner px-3 font-mono text-base transition-colors ${
                  unit === o.u ? "bg-accent text-on-accent" : "text-text-muted hover:text-text"}`}>
          {o.face}
        </button>
      ))}
    </div>
  );
}
