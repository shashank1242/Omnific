"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createEdit, listVersions, uploadInput } from "@/lib/api";
import type { EditSummary, VersionInfo } from "@/lib/types";
import { hasPending, versionChips } from "@/lib/versions";
import AttachmentPicker from "./AttachmentPicker";

/** Every version of this simulation, polled while any of them is still being built. */
export function useVersions(jobId: string) {
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  // poll until a first answer arrives (a failed first request retries), then while any version is building
  const pending = versions.length === 0 || hasPending(versions);
  useEffect(() => {
    let live = true;
    const load = () => listVersions(jobId).then((v) => live && setVersions(v)).catch(() => undefined);
    void load();
    const timer = pending ? setInterval(load, 3000) : undefined;
    return () => { live = false; if (timer) clearInterval(timer); };
  }, [jobId, pending]);
  return versions;
}

/** Quiet row of version chips: the page you are on is magenta, the live version carries a small label. */
export function VersionStrip({ versions, jobId }: { versions: VersionInfo[]; jobId: string }) {
  const chips = versionChips(versions, jobId);
  if (chips.length === 0) return null;
  return (
    <nav aria-label="Versions of this simulation" className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="mr-1 text-muted">Versions</span>
      {chips.map((c) => (
        <Link
          key={c.jobId}
          href={`/jobs/${c.jobId}`}
          title={c.title}
          aria-current={c.current ? "page" : undefined}
          className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 font-semibold transition-colors ${
            c.current ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:border-fg/40 hover:text-fg"
          }`}
        >
          {c.label}
          {c.live ? <span className="font-normal">live</span> : null}
          {c.progress ? <span className="font-normal text-muted motion-safe:animate-pulse">{c.progress}…</span> : null}
          {c.failed ? <span className="font-normal text-bad">failed</span> : null}
        </Link>
      ))}
    </nav>
  );
}

const card = "rounded-xl border border-line bg-surface p-4";

/** Type a change in plain words; a new version is built and its page opens. */
export function EditPanel({ jobId }: { jobId: string }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const request = text.trim();
    if (!request) { setError("Describe what you want changed first."); return; }
    setBusy(true);
    setError(null);
    try {
      const ids: string[] = [];
      for (const [i, f] of files.entries()) {
        setProgress(`Uploading ${i + 1} of ${files.length}…`);
        ids.push((await uploadInput(f)).input_id);
      }
      setProgress(null);
      const { job_id } = await createEdit(jobId, ids.length > 0 ? { request, input_ids: ids } : { request });
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the change");
      setProgress(null);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className={`${card} space-y-3`} aria-labelledby="edit-h">
      <div>
        <h3 id="edit-h" className="text-sm font-bold tracking-tight">Change this simulation</h3>
        <p className="mt-0.5 text-xs text-muted">Say what to change. It is built as a new version; this one stays as it is.</p>
      </div>
      <label className="block">
        <span className="sr-only">What to change</span>
        <textarea
          rows={2}
          value={text}
          maxLength={2000}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          placeholder="Make the ramp 30°, run it for 20 s, make the car red, put it on the Moon"
          className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-accent/70"
        />
      </label>
      {error ? <p role="alert" className="rounded-lg border border-bad/50 bg-bad/10 px-3 py-2 text-sm">{error}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <AttachmentPicker files={files} setFiles={setFiles} disabled={busy} />
        <button
          type="submit"
          disabled={busy || text.trim() === ""}
          className="ml-auto h-10 rounded-lg border border-line bg-surface px-4 text-sm font-semibold transition-colors hover:border-fg/40 disabled:opacity-50"
        >
          {progress ?? (busy ? "Starting…" : "Build new version")}
        </button>
      </div>
    </form>
  );
}

/** What this version changed against the one before it. Prose with numbers: body font. */
export function WhatChanged({ edit }: { edit: EditSummary }) {
  return (
    <section className={card} aria-labelledby="changed-h">
      <h3 id="changed-h" className="text-sm font-bold tracking-tight">What changed in v{edit.version}</h3>
      <blockquote className="mt-2 border-l-2 border-accent pl-3 text-sm">{edit.request}</blockquote>
      {edit.summary ? <p className="mt-2 text-sm text-muted">{edit.summary}</p> : null}
      {edit.diff.length > 0 ? (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm marker:text-muted">
          {edit.diff.map((line) => <li key={line}>{line}</li>)}
        </ul>
      ) : null}
      <p className="mt-2 text-xs text-muted">
        Compare it with the earlier version from the Tune tab.{" "}
        <Link href={`/jobs/${edit.base_job_id}`} className="text-accent underline underline-offset-2">Open v{edit.base_version ?? edit.version - 1}</Link>
      </p>
    </section>
  );
}
