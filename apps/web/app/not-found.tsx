import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl space-y-4 py-16">
      <p className="font-mono text-sm text-muted">404</p>
      <h1 className="text-2xl font-semibold tracking-display">Nothing here</h1>
      <p className="text-muted">
        This page doesn&rsquo;t exist, or the simulation link is incomplete. Simulation links look like
        <span className="font-mono text-fg"> /jobs/</span> followed by a 32-character id.
      </p>
      <div className="flex flex-wrap gap-2">
        <Link href="/" className="inline-flex h-10 items-center rounded-lg bg-flash px-5 text-sm font-bold text-flash-fg">
          New simulation
        </Link>
        <Link href="/explore" className="inline-flex h-10 items-center rounded-md border border-line px-5 text-sm">
          Explore the library
        </Link>
      </div>
    </div>
  );
}
