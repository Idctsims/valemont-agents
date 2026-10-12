import type { Metadata } from "next";

import { StatusChip, type LegStatus } from "@/components/ui/badges";
import { Fold } from "@/components/ui/fold";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { sourcesHealth, type SourceRow } from "@/lib/feed/data";
import { ago, chicagoMidnight, reasonLabel } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Sources" };

// Every feed source the worker fetches (workers/feed/sources.toml, synced
// into `sources`), how it is doing, and what the drop rules filtered today.
// Reads only, through RLS; the worker records every fetch on the row.

const FEED_INTERVAL_S = 30 * 60;

function status(r: SourceRow, now: number): { status: LegStatus; label: string } {
  if (!r.active) return { status: "dead", label: "Off" };
  if (r.consecutive_failures >= 6) return { status: "danger", label: "Failing" };
  if (r.consecutive_failures > 0) return { status: "on-pace", label: `${r.consecutive_failures} failed` };
  if (!r.last_ok_at) return { status: "on-pace", label: "Not yet" };
  // Two missed runs without a good fetch: stale, like the health monitor's rule.
  if (now - Date.parse(r.last_ok_at) > 2 * FEED_INTERVAL_S * 1000) return { status: "danger", label: "Stale" };
  return { status: "hit", label: "OK" };
}

const pillarLabel = (p: string[]) => p.map((x) => (x === "home" ? "Home" : "Sports")).join(" · ");

function SourceItem({ r, now }: { r: SourceRow; now: number }) {
  const s = status(r, now);
  return (
    <li data-testid="source-row" data-slug={r.slug} className="border-b border-border py-4">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="text-base text-text">{r.name}</span>
        <StatusChip status={s.status} label={s.label} />
      </div>
      <p className="mt-1 font-mono text-xs text-text-muted">
        {pillarLabel(r.pillars)} · {r.kind === "gnews" ? "Google News" : "RSS"}
      </p>
      <dl className="mt-3 grid grid-cols-4 gap-2 text-sm">
        <div>
          <dt className="label-mono text-text-muted">Last OK</dt>
          <dd className="mt-1 font-mono text-text">{ago(r.last_ok_at, now)}</dd>
        </div>
        <div>
          <dt className="label-mono text-text-muted">24h</dt>
          <dd data-testid="source-items" className="mt-1 font-mono text-text">{r.items24h}</dd>
        </div>
        <div>
          <dt className="label-mono text-text-muted">Filtered</dt>
          <dd className="mt-1 font-mono text-text">{r.filtered24h}</dd>
        </div>
        <div>
          <dt className="label-mono text-text-muted">Fails</dt>
          <dd className="mt-1 font-mono text-text">{r.consecutive_failures}</dd>
        </div>
      </dl>
      {r.decodeFailed24h > 0 && (
        <p className="mt-2 font-mono text-xs text-text-muted">{r.decodeFailed24h} Google News link(s) not decoded in 24h</p>
      )}
      {r.active && r.last_error && (
        <p className="mt-3 rounded-inner bg-surface-2 p-3 font-mono text-xs break-words text-text-muted">{r.last_error}</p>
      )}
      {!r.active && r.notes && <p className="mt-2 text-sm text-text-muted text-pretty">{r.notes}</p>}
    </li>
  );
}

export default async function SourcesPage() {
  await requireOwner();
  const supabase = await createClient();
  // Display only: the worker judges fetches on the database clock.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();
  const { rows, filtered, patterns } = await sourcesHealth(supabase, chicagoMidnight(new Date(now)));
  const active = rows.filter((r) => r.active);
  const inactive = rows.filter((r) => !r.active);
  const ok = active.filter((r) => status(r, now).status === "hit").length;

  return (
    <>
      <PageHeader
        label="Settings"
        title="Sources"
        summary="Every outlet the feed reads, fetched every 30 minutes. A source that fails six times in a row sends one push; its next good fetch re-arms it."
      />
      <p data-testid="sources-summary" className="mb-6 font-mono text-sm text-text-muted">
        {ok} of {active.length} active sources OK
      </p>

      <ul data-testid="sources-active" className="border-t border-border lg:max-w-3xl">
        {active.map((r) => (
          <SourceItem key={r.id} r={r} now={now} />
        ))}
      </ul>

      <div className="mt-8 flex flex-col gap-2 lg:max-w-3xl">
        <Fold label="Filtered today" count={filtered.length} testId="sources-filtered">
          {filtered.length === 0 ? (
            <li className="py-3 text-sm text-text-muted">Nothing filtered since midnight.</li>
          ) : (
            filtered.map((f) => (
              <li key={f.id} data-testid="filtered-row" className="border-b border-border py-3">
                {f.url ? (
                  <a href={f.url} target="_blank" rel="noopener noreferrer" className="text-sm text-text transition-colors hover:text-accent">
                    {f.title}
                  </a>
                ) : (
                  <span className="text-sm text-text">{f.title}</span>
                )}
                <p className="mt-1 font-mono text-xs text-text-muted">
                  {f.source_name} · {reasonLabel(f.filtered_reason, patterns)}
                </p>
              </li>
            ))
          )}
        </Fold>
        {inactive.length > 0 && (
          <Fold label="Inactive" count={inactive.length} testId="sources-inactive">
            {inactive.map((r) => (
              <SourceItem key={r.id} r={r} now={now} />
            ))}
          </Fold>
        )}
      </div>
    </>
  );
}
