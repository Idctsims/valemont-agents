"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { NAV, SYSTEM_PAGES } from "@/lib/nav";
import type { PageContext, Pillar } from "@/lib/wags/page-context";

// Wags on every page: the dock's centre mark (phone) and the rail button
// (desktop) open the sheet through this provider. The sheet and everything
// it needs (the AI SDK, streamdown) load on first open only, so no other
// route pays for them.

const WagsSheet = dynamic(() => import("./wags-sheet").then((m) => m.WagsSheet), { ssr: false });

type Ctx = {
  /** Open the sheet; with a context, open it on that instead of the page's. */
  open: (ctx?: Omit<PageContext, "route">) => void;
  isOpen: boolean;
  /** A page names itself more precisely than its path can (a venture's name). */
  setPageContext: (route: string, ctx: Omit<PageContext, "route">) => void;
};

const WagsContext = createContext<Ctx | null>(null);

export function useWags(): Ctx {
  const ctx = use(WagsContext);
  if (!ctx) throw new Error("useWags outside WagsProvider");
  return ctx;
}

const PILLAR_OF: Record<string, Pillar> = {
  "/": "goals",
  "/goals": "goals",
  "/ventures": "ventures",
  "/capital": "capital",
  "/brief": "brief",
  // Tech Hub (db/045 ctx_tech): official items of the last 7 days.
  "/tech": "tech",
  // Screening Room (db/051 ctx_screening): shelves, upcoming, new trailers.
  "/screening": "screening",
};

/** "Context · Brief": what Ask Wags about this opens with. */
export const BRIEF_CONTEXT = { label: "Brief", pillar: "brief" } as const;

/** What the chip says for a path, before (or without) a page's own label. */
export function derivePageContext(pathname: string): PageContext | null {
  if (pathname.startsWith("/wags")) return null;
  if (pathname.startsWith("/brief")) return { route: pathname, ...BRIEF_CONTEXT };
  const venture = pathname.match(/^\/ventures\/([a-z0-9-]+)/);
  if (venture) return { route: pathname, label: `Ventures · ${venture[1]}`, pillar: "ventures", slug: venture[1] };
  const pages = [
    ...NAV.flatMap((g) => g.pillars.flatMap((p) => [{ label: p.name, href: p.href }, ...(p.pages ?? [])])),
    ...SYSTEM_PAGES,
  ];
  const page = pages.find((p) => p.href === pathname);
  if (!page) return null;
  return { route: pathname, label: page.label, ...(PILLAR_OF[pathname] ? { pillar: PILLAR_OF[pathname] } : {}) };
}

const THREAD_KEY = "vm-wags-thread";

function storedThread(): string | null {
  try {
    return sessionStorage.getItem(THREAD_KEY);
  } catch {
    return null;
  }
}

export function WagsProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [isOpen, setOpen] = useState(false);
  const [named, setNamed] = useState<PageContext | null>(null);
  // A context the opener asked for (Ask Wags about this), for this opening only.
  const [forced, setForced] = useState<PageContext | null>(null);
  const [dropped, setDropped] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const opener = useRef<HTMLElement | null>(null);

  const page = useMemo(() => {
    if (dropped) return null;
    if (forced) return forced;
    if (named?.route === pathname) return named;
    return derivePageContext(pathname);
  }, [dropped, forced, named, pathname]);

  const open = useCallback((ctx?: Omit<PageContext, "route">) => {
    opener.current = document.activeElement as HTMLElement | null;
    setForced(ctx ? { route: window.location.pathname, ...ctx } : null);
    setThreadId((t) => t ?? storedThread() ?? crypto.randomUUID());
    setDropped(false);
    setOpen(true);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setForced(null);
    opener.current?.focus?.({ preventScroll: true });
  }, []);

  const setPageContext = useCallback(
    (route: string, ctx: Omit<PageContext, "route">) => setNamed({ route, ...ctx }),
    [],
  );

  const newThread = useCallback(() => {
    const id = crypto.randomUUID();
    setThreadId(id);
    setDropped(false);
    try {
      sessionStorage.removeItem(THREAD_KEY);
    } catch {}
  }, []);

  const rememberThread = useCallback((id: string) => {
    try {
      sessionStorage.setItem(THREAD_KEY, id);
    } catch {}
  }, []);

  // Navigating closes the sheet, like the More sheet.
  const [seen, setSeen] = useState(pathname);
  if (seen !== pathname) {
    setSeen(pathname);
    if (isOpen) setOpen(false);
  }

  const value = useMemo(() => ({ open, isOpen, setPageContext }), [open, isOpen, setPageContext]);

  return (
    <WagsContext value={value}>
      {children}
      {isOpen && threadId && (
        <WagsSheet
          threadId={threadId}
          page={page}
          onRemovePage={() => setDropped(true)}
          onClose={close}
          onNewThread={newThread}
          onStarted={rememberThread}
        />
      )}
    </WagsContext>
  );
}

/** Rendered by a page that can name itself, e.g. a venture: "Ventures · Sail Beach Club". */
export function WagsPageContext({ label, pillar, slug }: { label: string; pillar?: Pillar; slug?: string }) {
  const { setPageContext } = useWags();
  const pathname = usePathname();
  useEffect(() => {
    setPageContext(pathname, { label, ...(pillar ? { pillar } : {}), ...(slug ? { slug } : {}) });
  }, [setPageContext, pathname, label, pillar, slug]);
  return null;
}
