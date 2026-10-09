"use server";

import { refresh } from "next/cache";

import { requireOwner } from "@/lib/auth";
import { AREAS, localToday, periodFor, type Area, type Horizon } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";

// Every action re-checks the owner: a Server Action is a POST to whatever
// route it lives on, so proxy coverage alone is never trusted. Writes go
// through the owner's session, so db/021's RLS and column grants apply too:
// carry history (period, carried_from, carry_count) is not writable here.

const HORIZONS: Horizon[] = ["weekly", "monthly", "long_term"];

function cleanTitle(title: unknown): string {
  const t = typeof title === "string" ? title.trim().replace(/\s+/g, " ") : "";
  if (t.length < 1 || t.length > 200) throw new Error("A goal needs a title of 1 to 200 characters.");
  return t;
}

function cleanArea(area: unknown): Area | null {
  if (area === null || area === undefined || area === "") return null;
  if (!AREAS.includes(area as Area)) throw new Error("Unknown area.");
  return area as Area;
}

function cleanId(id: unknown): string {
  if (typeof id !== "string" || !/^[0-9a-f-]{36}$/i.test(id)) throw new Error("Unknown goal.");
  return id;
}

async function owner() {
  await requireOwner();
  return createClient();
}

export async function createGoal(input: { title: string; horizon: Horizon; area: Area | null }) {
  const supabase = await owner();
  if (!HORIZONS.includes(input.horizon)) throw new Error("Unknown horizon.");
  const { error } = await supabase.from("goals").insert({
    title: cleanTitle(input.title),
    horizon: input.horizon,
    area: cleanArea(input.area),
    // The current period in the owner's timezone, decided here, never by
    // the phone's clock.
    period_start: periodFor(input.horizon, localToday()),
  });
  if (error) throw new Error(`Couldn't add the goal: ${error.message}`);
  refresh();
}

async function setStatus(id: unknown, status: "open" | "done" | "dropped") {
  const supabase = await owner();
  const { error, count } = await supabase
    .from("goals")
    .update(
      { status, completed_at: status === "done" ? new Date().toISOString() : null },
      { count: "exact" },
    )
    .eq("id", cleanId(id));
  if (error) throw new Error(`Couldn't update the goal: ${error.message}`);
  if (count === 0) throw new Error("That goal no longer exists.");
  refresh();
}

export async function completeGoal(id: string) {
  await setStatus(id, "done");
}

/** Back to open, from done or from dropped. */
export async function uncompleteGoal(id: string) {
  await setStatus(id, "open");
}

export async function dropGoal(id: string) {
  await setStatus(id, "dropped");
}

export async function editGoal(id: string, input: { title: string; area: Area | null }) {
  const supabase = await owner();
  const { error, count } = await supabase
    .from("goals")
    .update({ title: cleanTitle(input.title), area: cleanArea(input.area) }, { count: "exact" })
    .eq("id", cleanId(id));
  if (error) throw new Error(`Couldn't save the goal: ${error.message}`);
  if (count === 0) throw new Error("That goal no longer exists.");
  refresh();
}

/** Carry one goal to the next week (or month) now, before its period ends. */
export async function moveGoalToNext(id: string) {
  const supabase = await owner();
  const { error } = await supabase.rpc("carry_goal", { p_goal: cleanId(id) });
  if (error) throw new Error(`Couldn't move the goal: ${error.message}`);
  refresh();
}
