"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { FidelityBadge } from "@/components/Panels";
import Icon from "@/components/Icon";
import StrobeThumb from "@/components/StrobeThumb";
import { ApiError, artifactUrl, listGallery, thumbnailUrl } from "@/lib/api";
import type { PublishedSimulation } from "@/lib/types";

const PAGE = 24;

function Card({ sim }: { sim: PublishedSimulation }) {
  // the 3D view captured when it was published; the motion sketch when there is none (or it fails to load)
  const [picture, setPicture] = useState(sim.has_thumbnail);
  return (
    <Link href={`/jobs/${sim.job_id}`} className="group flex h-full flex-col bg-surface">
      <div className="aspect-video w-full overflow-hidden bg-[var(--studio)]">
        {picture ? (
          // eslint-disable-next-line @next/next/no-img-element -- a small same-origin data image; no optimisation needed
          <img src={thumbnailUrl(sim.job_id)} alt="" loading="lazy" onError={() => setPicture(false)}
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.04]" />
        ) : (
          <StrobeThumb playbackUrl={artifactUrl(sim.job_id, "playback.json")} className="transition-transform duration-500 group-hover:scale-[1.04]" />
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <h2 className="font-semibold leading-snug group-hover:text-accent">{sim.title}</h2>
          <FidelityBadge fidelity={sim.fidelity} />
        </div>
        <p className="line-clamp-3 text-sm text-muted">{sim.description || sim.prompt}</p>
        {sim.domains.length > 0 ? (
          <ul className="mt-auto flex flex-wrap gap-1.5 pt-1" aria-label="Domains">
            {sim.domains.map((d) => <li key={d} className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">{d}</li>)}
          </ul>
        ) : null}
      </div>
    </Link>
  );
}

export default function ExplorePage() {
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [sims, setSims] = useState<PublishedSimulation[] | null>(null);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0); // bumped per search, so late responses for an older one are dropped

  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    let live = true;
    generation.current += 1;
    setLoading(true);
    listGallery(q, PAGE, 0)
      .then((r) => { if (!live) return; setSims(r); setMore(r.length === PAGE); setError(null); })
      .catch((e: unknown) => {
        if (!live) return;
        setSims([]);
        setError(e instanceof ApiError && e.status === 503 ? "The public library isn't available on this server." : e instanceof Error ? e.message : String(e));
      })
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [q]);

  async function loadMore() {
    if (!sims) return;
    const gen = generation.current;
    setLoading(true);
    try {
      const r = await listGallery(q, PAGE, sims.length);
      if (gen !== generation.current) return;
      setError(null);
      setSims((prev) => [...(prev ?? []), ...r]);
      setMore(r.length === PAGE);
    } catch (e) {
      if (gen === generation.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (gen === generation.current) setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <section className="max-w-3xl">
        <h1 className="text-[2.4rem] font-extrabold leading-none tracking-display sm:text-5xl">Public library</h1>
        <p className="mt-3 text-lg text-muted">Simulations people built and chose to share. Open one to play it, strobe it and see how it was checked.</p>
        <div className="relative mt-6">
          <Icon name="search" size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search pendulums, rockets, heat flow…"
          aria-label="Search the public library"
          className="w-full rounded-xl border border-line bg-surface py-3 pl-11 pr-4 text-base outline-none placeholder:text-muted/80 focus:border-accent/70"
        />
        </div>
      </section>
      {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
      {sims === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : sims.length === 0 && !error ? (
        <p className="text-sm text-muted">
          {q ? "Nothing matches that search yet." : "Nothing has been published yet."}{" "}
          <Link href="/" className="text-accent underline underline-offset-2">Build a simulation</Link> and publish it once you accept it.
        </p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {sims.map((s) => <li key={s.job_id} className="overflow-hidden rounded-2xl border border-line transition-colors hover:border-fg/30"><Card sim={s} /></li>)}
        </ul>
      )}
      {more ? (
        <button type="button" disabled={loading} onClick={loadMore} className="h-10 rounded-lg border border-line bg-surface px-4 text-sm font-semibold hover:border-fg/40 disabled:opacity-50">
          {loading ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </div>
  );
}
