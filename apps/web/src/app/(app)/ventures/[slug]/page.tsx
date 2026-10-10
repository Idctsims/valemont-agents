import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { VentureDetail } from "@/components/ventures/venture-detail";
import { WagsPageContext } from "@/components/wags/wags-provider";
import { requireOwner } from "@/lib/auth";
import { localToday } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";
import { ventureBySlug } from "@/lib/ventures/data";

export async function generateMetadata({ params }: PageProps<"/ventures/[slug]">): Promise<Metadata> {
  await requireOwner();
  const supabase = await createClient();
  const { data } = await supabase.from("ventures").select("name").eq("slug", (await params).slug).maybeSingle();
  return { title: data?.name ?? "Venture" };
}

export default async function VenturePage({ params, searchParams }: PageProps<"/ventures/[slug]">) {
  await requireOwner();
  const { slug } = await params;
  const supabase = await createClient();
  const today = localToday();
  const detail = await ventureBySlug(supabase, slug, today);
  if (!detail) notFound();

  return (
    <>
      {/* Wags's chip names the venture: "Context · Ventures · Sail Beach Club". */}
      <WagsPageContext label={`Ventures · ${detail.venture.name}`} pillar="ventures" slug={slug} />
      <VentureDetail
      // Remount on a new server snapshot of a different venture only.
      key={detail.venture.id}
      {...detail}
      today={today}
      startEditing={(await searchParams).edit === "1"}
      />
    </>
  );
}
