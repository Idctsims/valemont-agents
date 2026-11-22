import { expect, test } from "@playwright/test";

import { nextShelf } from "../src/lib/screening/types";
import { makersOf, runtimeOf } from "../src/lib/tmdb/credits";

// The Screening Room as Letterboxd (owner, 2026-11-22; docs/screening.md §9):
// a lit shelf button turns off, another one moves the title; who made it.

test.describe("the shelf buttons", () => {
  test("tapping the lit one takes the title off; another moves it", () => {
    expect(nextShelf("want", "want")).toBeNull();
    expect(nextShelf("want", "finished")).toBe("finished");
    expect(nextShelf(null, "watching")).toBe("watching");
    expect(nextShelf("finished", "finished")).toBeNull();
  });
});

test.describe("who made it", () => {
  test("a film's directors from its crew, at most two, each once", () => {
    const crew = [
      { job: "Producer", name: "Emma Thomas" },
      { job: "Director", name: "Christopher Nolan" },
      { job: "Director", name: "Christopher Nolan" },
      { job: "Director", name: "Second Person" },
      { job: "Director", name: "Third Person" },
    ];
    expect(makersOf("movie", { credits: { crew } })).toEqual({ label: "Directed by", names: ["Christopher Nolan", "Second Person"] });
  });

  test("a series' creators; nothing when TMDB has none", () => {
    expect(makersOf("tv", { created_by: [{ name: "Jesse Armstrong" }] })).toEqual({ label: "Created by", names: ["Jesse Armstrong"] });
    expect(makersOf("tv", {})).toBeNull();
    expect(makersOf("movie", { credits: { crew: [] } })).toBeNull();
    expect(makersOf("movie", { credits: "x" })).toBeNull();
  });

  test("runtime: a film's minutes, a series' first episode length, else none", () => {
    expect(runtimeOf("movie", { runtime: 158 })).toBe(158);
    expect(runtimeOf("tv", { episode_run_time: [58, 62] })).toBe(58);
    expect(runtimeOf("tv", { episode_run_time: [] })).toBeNull();
    expect(runtimeOf("movie", { runtime: 0 })).toBeNull();
  });
});
