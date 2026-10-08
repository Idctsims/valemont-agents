"use client";

import {
  Command,
  CurrencyBtc,
  Cpu,
  FilmSlate,
  Football,
  type IconProps,
} from "@phosphor-icons/react";

import type { SectionKey } from "@/lib/sitemap";

const ICONS: Record<SectionKey, React.ComponentType<IconProps>> = {
  command: Command,
  culture: FilmSlate,
  tech: Cpu,
  sports: Football,
  crypto: CurrencyBtc,
};

export function SectionIcon({ section, ...props }: { section: SectionKey } & IconProps) {
  const Icon = ICONS[section];
  return <Icon aria-hidden {...props} />;
}
