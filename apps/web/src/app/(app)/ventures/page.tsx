import type { Metadata } from "next";

import { Fold } from "@/components/ui/fold";
import { NewVenture } from "@/components/ventures/new-venture";
import { VentureRow } from "@/components/ventures/venture-row";
import { requireOwner } from "@/lib/auth";
import { localToday } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";
import { listVentures } from "@/lib/ventures/data";
import { ledgerOrder } from "@/lib/ventures/types";

export const metadata: Metadata = { title: "Ventures" };

export default async function VenturesPage() {
  await requireOwner();
  const supabase = await createClient();
  const today = localToday();
  const all = await listVentures(supabase);
  const live = ledgerOrder(all.filter((v) => !v.archived_at));
  const archived = ledgerOrder(all.filter((v) => v.archived_at));

  return (
    <div className="lg:max-w-3xl">
      <header className="mb-8 lg:mb-12">
        <h1 className="font-display text-4xl text-text lg:text-display">Ventures</h1>
      </header>

      <ul data-testid="ventures" className="border-t border-border">
        {live.map((v) => (
          <VentureRow key={v.id} venture={v} openDates={v.open_dates} today={today} />
        ))}
      </ul>
      {archived.length > 0 && (
        <Fold label="Archived" count={archived.length} testId="ventures-archived">
          {archived.map((v) => (
            <VentureRow key={v.id} venture={v} openDates={v.open_dates} today={today} />
          ))}
        </Fold>
      )}
      <NewVenture />
    </div>
  );
}
