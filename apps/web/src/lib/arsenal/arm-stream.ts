// Arm Me's stream as the page reads it (Chat 5 Phase 2; docs/arsenal.md
// §9.3): the card as stored and the route's NDJSON lines. Client-safe (no
// voice text, no schema library), so the box ships light.

import type { Category, Room, Verification } from "./types";

export const MAX_SITUATION = 600;

/** A card as stored (db/059 arsenal_arm_card's answer), shown on the page. */
export type ArmCard = {
  id: number;
  canonical_key: string;
  category: Category;
  title: string;
  origin: string;
  meaning: string;
  deploy_line: string;
  room: Room;
  source_work: string;
  quote: string | null;
  quote_by: string | null;
  source_url: string | null;
  verification: Verification;
  verification_note: string | null;
  status: "seed" | "pool" | "dropped" | "rejected" | "arm";
  is_test: boolean;
  existing: boolean;
  held: boolean;
  saved: boolean;
  /** A seed, a dropped reference or a saved one: "From your Arsenal". */
  in_arsenal: boolean;
};

/** The route's NDJSON lines, one JSON object a line. */
export type ArmLine =
  | { type: "start"; model: string; budgetMode: boolean }
  | { type: "card"; card: ArmCard }
  | { type: "done"; requestId: number | null; note: string | null }
  | { type: "error"; message: string };

/** Parse one NDJSON line from the route (the page's reader). */
export function parseArmLine(line: string): ArmLine | null {
  if (!line.trim()) return null;
  try {
    const v = JSON.parse(line) as ArmLine;
    return v && typeof v === "object" && "type" in v ? v : null;
  } catch {
    return null;
  }
}
