"use client";

import Link from "next/link";
import { useEffect } from "react";

/** Any page that throws while rendering lands here instead of a blank screen. */
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div role="alert" className="mx-auto max-w-xl space-y-4 py-16">
      <h1 className="text-2xl font-semibold tracking-display">Something went wrong on this page</h1>
      <p className="text-muted">
        Your simulation is not affected: builds run on the server and keep going. Try again, or start from the home page.
      </p>
      {error.digest ? <p className="font-mono text-xs text-muted">Reference: {error.digest}</p> : null}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={reset} className="h-10 rounded-lg bg-flash px-5 text-sm font-bold text-flash-fg">
          Try again
        </button>
        <Link href="/" className="inline-flex h-10 items-center rounded-md border border-line px-5 text-sm">
          Home
        </Link>
      </div>
    </div>
  );
}
