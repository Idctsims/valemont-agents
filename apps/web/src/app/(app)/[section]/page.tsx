import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PaperBadge } from "@/components/ui/badges";
import { PageHeader } from "@/components/ui/page-header";
import { getSection, pad2 } from "@/lib/sitemap";

export async function generateMetadata({
  params,
}: PageProps<"/[section]">): Promise<Metadata> {
  const section = getSection((await params).section);
  return { title: section?.name ?? "Not found" };
}

export default async function SectionPage({ params }: PageProps<"/[section]">) {
  const section = getSection((await params).section);
  if (!section) notFound();

  const first = section.pillars[0];
  const last = section.pillars[section.pillars.length - 1];
  const range =
    first === last ? `Pillar ${pad2(first.n)}` : `Pillars ${pad2(first.n)}–${pad2(last.n)}`;
  // Bento: with three or more pillars, the first one takes the wide slot.
  const feature = section.pillars.length >= 3;

  return (
    <>
      <PageHeader label={range} title={section.name} summary={section.summary} />

      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {section.pillars.map((p, i) => {
          const wide = feature && i === 0;
          return (
            <li key={p.slug} className={wide ? "sm:col-span-2" : ""}>
              <Link
                href={`/${section.key}/${p.slug}`}
                className="group flex h-full flex-col rounded-card border border-border bg-surface p-6 transition-colors motion-card hover:bg-surface-2 lg:p-8"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="label-mono text-accent">{pad2(p.n)}</span>
                  {p.money && <PaperBadge />}
                </div>
                <h2
                  className={`mt-6 font-display text-text text-balance ${wide ? "text-4xl" : "text-3xl"}`}
                >
                  {p.name}
                </h2>
                <p className="mt-3 max-w-measure text-base text-text-muted text-pretty">
                  {p.summary}
                </p>
                <p className="label-mono mt-auto pt-8 text-text-muted">{p.arrives}</p>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}
