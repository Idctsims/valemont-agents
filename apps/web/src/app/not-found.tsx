import Link from "next/link";

import { button } from "@/components/ui/button";

import { signature } from "./fonts";

export default function NotFound() {
  return (
    <div
      className={`${signature.variable} flex min-h-dvh flex-col items-center justify-center bg-bg px-safe text-center`}
    >
      <p className="font-signature text-display text-accent">404</p>
      <h1 className="mt-6 font-display text-3xl text-text text-balance">
        There is no page at this address.
      </h1>
      <p className="mt-3 max-w-measure text-base text-text-muted">
        Check the link, or start again from Command.
      </p>
      <Link href="/" className={`${button.primary} mt-8`}>
        Go to Command
      </Link>
    </div>
  );
}
