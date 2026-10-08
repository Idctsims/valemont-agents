import type { Metadata } from "next";

import { StatusChip, type LegStatus } from "@/components/ui/badges";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "System health" };

type Row = {
  job: string;
  expected_interval_s: number;
  last_ok_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  alert_state: "ok" | "alerted";
  updated_at: string;
};

function ago(iso: string | null, now: number): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 90) return `${s}s ago`;
  if (s < 90 * 60) return `${Math.round(s / 60)}m ago`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function every(s: number): string {
  return s % 86400 === 0 ? `${s / 86400}d` : s % 3600 === 0 ? `${s / 3600}h` : s % 60 === 0 ? `${s / 60}m` : `${s}s`;
}

// Same rule as the worker's health monitor (core/system_jobs.py): two
// failures in a row, or no success within twice the expected interval.
function status(r: Row, now: number): { status: LegStatus; label: string } {
  if (r.consecutive_failures >= 2) return { status: "danger", label: "Failing" };
  const ref = Date.parse(r.last_ok_at ?? r.updated_at);
  if (now - ref > 2 * r.expected_interval_s * 1000) return { status: "danger", label: "Stale" };
  if (r.consecutive_failures === 1) return { status: "on-pace", label: "1 failure" };
  return { status: "hit", label: "OK" };
}

export default async function HealthPage() {
  await requireOwner();
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("job_health")
    .select("job, expected_interval_s, last_ok_at, last_error, consecutive_failures, alert_state, updated_at")
    .order("job");
  const rows = (data ?? []) as Row[];
  // Display only: the worker judges health on the database clock.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();

  return (
    <>
      <PageHeader
        label="Settings"
        title="System health"
        summary="Every job the worker runs, and the external watchdog. Alerts arrive by push when a job fails twice in a row or misses twice its interval, and again when it recovers."
      />
      {error ? (
        <p className="text-base text-danger">Couldn&apos;t read job health: {error.message}</p>
      ) : rows.length === 0 ? (
        <p className="text-base text-text-muted">
          No jobs have reported yet. They appear once the worker runs its first heartbeat.
        </p>
      ) : (
        <ul className="grid gap-3">
          {rows.map((r) => {
            const s = status(r, now);
            return (
              <li key={r.job} className="rounded-card border border-border bg-surface p-5 lg:p-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="font-mono text-lg text-text">{r.job}</span>
                  <div className="flex items-center gap-2">
                    {r.alert_state === "alerted" && <StatusChip status="live" label="Alerted" />}
                    <StatusChip status={s.status} label={s.label} />
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
                  <div>
                    <dt className="label-mono text-text-muted">Last OK</dt>
                    <dd className="mt-1 font-mono text-text">{ago(r.last_ok_at, now)}</dd>
                  </div>
                  <div>
                    <dt className="label-mono text-text-muted">Every</dt>
                    <dd className="mt-1 font-mono text-text">{every(r.expected_interval_s)}</dd>
                  </div>
                  <div>
                    <dt className="label-mono text-text-muted">Failures</dt>
                    <dd className="mt-1 font-mono text-text">{r.consecutive_failures}</dd>
                  </div>
                </dl>
                {r.last_error && (
                  <p className="mt-4 rounded-inner bg-surface-2 p-3 font-mono text-xs break-words text-text-muted">
                    {r.last_error}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
