import type { Metadata } from "next";

import { CapitalView } from "@/components/capital/capital-view";
import { requireOwner } from "@/lib/auth";
import { capitalData } from "@/lib/capital/data";
import { localToday } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Capital" };

export default async function CapitalPage() {
  await requireOwner();
  const supabase = await createClient();
  return <CapitalView data={await capitalData(supabase)} today={localToday()} />;
}
