import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy policy",
  description: "How Valemont Command handles Pinterest and other account data.",
  // The one indexable page in the app.
  robots: { index: true, follow: false },
};

const UPDATED = "October 8, 2026";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-border pt-6">
      <h2 className="font-display text-2xl text-text">{title}</h2>
      <div className="mt-3 flex flex-col gap-3 text-base text-text-muted">{children}</div>
    </section>
  );
}

export default function PrivacyPage() {
  return (
    <main className="mx-auto w-full max-w-measure px-safe pt-safe pb-safe">
      <header className="pt-16 pb-10">
        <p className="label-mono text-text-muted">Valemont Command · updated {UPDATED}</p>
        <h1 className="mt-3 font-display text-4xl text-text">Privacy policy</h1>
        <p className="mt-4 text-lg text-text-muted text-pretty">
          Valemont Command is a private dashboard built and used by one person, its owner. There
          are no public accounts and no sign-up: only the owner can sign in.
        </p>
      </header>

      <div className="flex flex-col gap-8 pb-16">
        <Section title="Pinterest data">
          <p>
            When the owner connects their own Pinterest account, the app requests read-only access
            with the scopes <span className="font-mono text-text">boards:read</span>,{" "}
            <span className="font-mono text-text">pins:read</span> and{" "}
            <span className="font-mono text-text">user_accounts:read</span>. It uses them to show the
            owner&apos;s own boards and pins inside the app, organised by board.
          </p>
          <p>
            The app never creates, edits or deletes pins or boards, never posts on the owner&apos;s
            behalf and never reads any other Pinterest user&apos;s private data.
          </p>
        </Section>

        <Section title="What is stored">
          <p>
            Pin and board metadata (identifiers, titles, descriptions, board names, links and image
            URLs) is stored in a private database. Images stay on Pinterest and are referenced by URL,
            not copied. The OAuth access and refresh tokens are stored encrypted and are only used
            server-side.
          </p>
          <p>
            To organise saved images, the app may send an image to Anthropic&apos;s Claude API to
            generate descriptive tags (style, colours, pieces). Those tags are stored with the pin.
          </p>
        </Section>

        <Section title="What is never done">
          <p>
            Data is not sold, rented or shared with anyone for advertising or any other purpose. The
            app carries no ads and no third-party analytics or tracking.
          </p>
        </Section>

        <Section title="Service providers">
          <p>
            The app runs on Vercel (web hosting), Supabase (database and sign-in) and Railway
            (background jobs), and uses Anthropic (image tagging). Each processes data only to run
            the app.
          </p>
        </Section>

        <Section title="Cookies">
          <p>
            Two kinds: a sign-in session cookie, required to use the app, and a cookie that remembers
            the light or dark theme. Neither is used for tracking.
          </p>
        </Section>

        <Section title="Retention and deletion">
          <p>
            Access can be revoked at any time from Pinterest&apos;s settings under connected apps.
            On disconnection, stored tokens are deleted, and Pinterest data is deleted on request.
          </p>
        </Section>

        <Section title="Changes">
          <p>
            If this policy changes, the date at the top of this page changes with it.
          </p>
        </Section>
      </div>
    </main>
  );
}
