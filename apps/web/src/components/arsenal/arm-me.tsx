"use client";

import { useEffect, useRef, useState } from "react";

import { RefCardBody } from "@/components/arsenal/ref-card";
import { SaveButton } from "@/components/arsenal/save-button";
import { button } from "@/components/ui/button";
import { ChipFace } from "@/components/ui/chip";
import { MAX_SITUATION, parseArmLine, type ArmCard } from "@/lib/arsenal/arm-stream";
import { checkTagKind, ROOM_LABEL, ROOMS, type Room } from "@/lib/arsenal/types";

// Arm Me (Chat 5 Phase 2; docs/arsenal.md §9.3): describe the room, pick
// one optionally, and three references stream in, card 1 as soon as it is
// written. Each is the same card as the daily drop's, with Save. A new card
// is "Unchecked until saved"; saving it sends it to the verifier ("Checking").
// A card that already exists comes back with its real tag, and one the
// owner already has is marked "From your Arsenal". A refusal (rate limit,
// budget, input) is the route's own sentence.

type Status = "idle" | "arming" | "done";

export function ArmMe() {
  const [situation, setSituation] = useState("");
  const [room, setRoom] = useState<Room | null>(null);
  const [cards, setCards] = useState<ArmCard[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [budgetMode, setBudgetMode] = useState(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  async function arm(e: React.FormEvent) {
    e.preventDefault();
    if (!situation.trim() || status === "arming") return;
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setCards([]);
    setError(null);
    setNote(null);
    setBudgetMode(false);
    setStatus("arming");
    try {
      const res = await fetch("/api/arsenal/arm", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ situation, room }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) {
        setError((await res.text()) || "Arm Me didn't answer. Try again.");
        setStatus("idle");
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (value) buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = done ? "" : lines.pop() ?? "";
        for (const line of done ? [...lines, buffer] : lines) {
          const msg = parseArmLine(line);
          if (!msg) continue;
          if (msg.type === "start") setBudgetMode(msg.budgetMode);
          else if (msg.type === "card") setCards((cs) => [...cs, msg.card]);
          else if (msg.type === "done") setNote(msg.note);
          else if (msg.type === "error") setError(msg.message);
        }
        if (done) break;
      }
      setStatus("done");
    } catch {
      if (!ctrl.signal.aborted) setError("The connection dropped. What arrived is above; try again for the rest.");
      setStatus("done");
    }
  }

  const left = MAX_SITUATION - situation.length;
  return (
    <section aria-label="Arm Me" data-testid="arm-me" className="mb-10 border-y border-border py-6 lg:max-w-3xl">
      <form data-testid="arm-form" onSubmit={arm}>
        <label htmlFor="arm-situation" className="label-mono text-text-muted">Arm Me</label>
        <textarea id="arm-situation" data-testid="arm-situation" value={situation} rows={3} maxLength={MAX_SITUATION}
                  onChange={(e) => setSituation(e.target.value)} placeholder="Describe the room"
                  aria-describedby="arm-count"
                  className="mt-2 block w-full resize-y rounded-inner border border-border bg-surface px-3 py-2 text-base text-text placeholder:text-text-muted focus:border-accent focus:outline-none" />
        <p id="arm-count" className={`mt-1 text-right font-mono text-xs ${left < 40 ? "text-accent" : "text-text-muted"}`}>
          {left} left
        </p>
        <div role="radiogroup" aria-label="Room (optional)" className="-ml-1 mt-1 flex flex-wrap gap-x-1">
          {ROOMS.map((r) => (
            <button key={r} type="button" role="radio" aria-checked={room === r} data-testid={`arm-room-${r}`}
                    onClick={() => setRoom(room === r ? null : r)} className="tap group inline-flex items-center px-1">
              <ChipFace selected={room === r} mono>{ROOM_LABEL[r]}</ChipFace>
            </button>
          ))}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button type="submit" data-testid="arm-submit" disabled={!situation.trim() || status === "arming"}
                  className={button.primary}>
            {status === "arming" ? "Arming" : "Arm me"}
          </button>
          {status === "arming" && (
            <p data-testid="arm-status" role="status" className="label-mono text-text-muted">
              {cards.length} of 3{budgetMode ? " · budget mode" : ""}
            </p>
          )}
        </div>
      </form>

      {error && <p role="alert" data-testid="arm-error" className="mt-4 text-sm text-danger">{error}</p>}
      {note && <p data-testid="arm-note" className="mt-4 text-sm text-text-muted">{note}</p>}

      {cards.length > 0 && (
        <ol data-testid="arm-cards" aria-busy={status === "arming"} className="mt-4">
          {cards.map((c) => <ArmCardItem key={c.id} c={c} />)}
        </ol>
      )}
    </section>
  );
}

function ArmCardItem({ c }: { c: ArmCard }) {
  const [saved, setSaved] = useState(c.saved);
  return (
    <li data-testid="arm-card" data-ref-id={c.id} data-key={c.canonical_key} className="border-b border-border py-6 last:border-b-0">
      <RefCardBody c={{
        category: c.category, title: c.title, sourceWork: c.source_work, origin: c.origin, quote: c.quote,
        quoteBy: c.quote_by, meaning: c.meaning, deployLine: c.deploy_line, room: c.room, sourceUrl: c.source_url,
        tag: checkTagKind({ verification: c.verification, status: c.status, saved, held: c.held }),
        tagNote: c.verification_note, isTest: c.is_test, fromArsenal: c.in_arsenal,
      }}>
        <div className="-ml-2 mt-3 flex">
          <SaveButton refId={c.id} saved={saved} label={c.title} onChange={setSaved} />
        </div>
      </RefCardBody>
    </li>
  );
}
