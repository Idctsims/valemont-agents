import { CaretLeft } from "@phosphor-icons/react/ssr";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PaperBadge, StatusChip } from "@/components/ui/badges";
import { chip } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { getPillar, getSection, pad2 } from "@/lib/sitemap";

export async function generateMetadata({
  params,
}: PageProps<"/[section]/[pillar]">): Promise<Metadata> {
  const { section, pillar } = await params;
  return { title: getPillar(section, pillar)?.name ?? "Not found" };
}

export default async function PillarPage({ params }: PageProps<"/[section]/[pillar]">) {
  const { section: sectionKey, pillar: slug } = await params;
  const section = getSection(sectionKey);
  const pillar = getPillar(sectionKey, slug);
  if (!section || !pillar) notFound();

  return (
    <>
      <Link
        href={`/${section.key}`}
        className="tap -ml-3 mb-4 inline-flex items-center gap-1.5 rounded-pill px-3 text-sm text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
      >
        <CaretLeft size={16} aria-hidden />
        {section.name}
      </Link>

      <PageHeader label={`Pillar ${pad2(pillar.n)} of 16`} title={pillar.name} summary={pillar.summary}>
        {section.pillars.length > 1 && (
          <nav aria-label={`${section.name} pillars`} className="-mx-4 mt-6 overflow-x-auto px-4">
            <ul className="flex w-max gap-2">
              {section.pillars.map((p) => (
                <li key={p.slug}>
                  <Link
                    href={`/${section.key}/${p.slug}`}
                    aria-current={p.slug === pillar.slug ? "page" : undefined}
                    className={chip(p.slug === pillar.slug)}
                  >
                    {p.name}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </PageHeader>

      <section
        aria-label="Build status"
        className="grid gap-6 rounded-card border border-border bg-surface p-6 lg:grid-cols-5 lg:gap-10 lg:p-10"
      >
        <div className="lg:col-span-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status="dead" label="Not built" />
            {pillar.money && <PaperBadge />}
          </div>
          <p className="mt-6 font-display text-3xl text-text text-balance">
            Arrives in {pillar.arrives}.
          </p>
          <p className="mt-3 max-w-measure text-base text-text-muted text-pretty">
            {pillar.money
              ? "Starts on paper. Paper and live are recorded separately and never blended into one number."
              : "This page fills in when its build chat ships. Nothing here is mocked."}
          </p>
        </div>

        <div className="rounded-inner bg-surface-2 p-5 lg:col-span-3 lg:p-6">
          <h2 className="label-mono text-text-muted">What lands here</h2>
          <ul className="mt-4 divide-y divide-border">
            {pillar.holds.map((h) => (
              <li key={h} className="flex gap-3 py-3 text-base text-text">
                <span aria-hidden className="mt-2.5 h-px w-4 shrink-0 bg-wood" />
                {h}
              </li>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
