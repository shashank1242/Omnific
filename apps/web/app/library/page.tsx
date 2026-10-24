"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, deleteDocument, listDocuments, uploadDocument } from "@/lib/api";
import type { DocumentStatus, LibraryDocument } from "@/lib/types";
import { ACCEPT_ATTR, validateUpload } from "@/lib/upload";

const BADGE: Record<DocumentStatus, string> = {
  pending: "border-line text-muted",
  ingesting: "border-accent text-accent",
  ready: "border-ok text-ok",
  failed: "border-bad text-bad",
};

function errMsg(e: unknown): string {
  if (e instanceof ApiError && e.status === 503) {
    return "The document library is not configured on this server (RAG database unavailable).";
  }
  return e instanceof Error ? e.message : String(e);
}

export default function LibraryPage() {
  const [docs, setDocs] = useState<LibraryDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [uploads, setUploads] = useState<Record<string, { name: string; p: number }>>({});
  const [dragging, setDragging] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setHighlight(new URLSearchParams(window.location.search).get("doc"));
  }, []);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
      setError(null);
    } catch (e) {
      setError(errMsg(e));
      setDocs((d) => d ?? []);
    }
  }, []);

  const active = docs?.some((d) => d.status === "pending" || d.status === "ingesting") ?? false;
  useEffect(() => {
    if (docs === null) { void refresh(); return; }
    if (!active) return;
    const t = setTimeout(() => void refresh(), 2000);
    return () => clearTimeout(t);
  }, [docs, active, refresh]);

  useEffect(() => {
    if (highlight && docs) document.getElementById(`doc-${highlight}`)?.scrollIntoView({ block: "center" });
  }, [highlight, docs]);

  async function upload(files: FileList | File[]) {
    const bad: string[] = [];
    const good: File[] = [];
    for (const f of Array.from(files)) {
      const m = validateUpload(f);
      if (m) bad.push(m); else good.push(f);
    }
    setProblems(bad);
    await Promise.all(good.map(async (f, i) => {
      const key = `${i}-${f.name}-${f.size}-${f.lastModified}`;
      setUploads((u) => ({ ...u, [key]: { name: f.name, p: 0 } }));
      try {
        await uploadDocument(f, (p) => setUploads((u) => ({ ...u, [key]: { name: f.name, p } })));
        setAnnounce(`${f.name} uploaded`);
      } catch (e) {
        setProblems((p) => [...p, `${f.name}: ${errMsg(e)}`]);
      } finally {
        setUploads((u) => { const n = { ...u }; delete n[key]; return n; });
      }
    }));
    await refresh();
  }

  async function remove(id: string) {
    try {
      await deleteDocument(id);
      setAnnounce("Document deleted");
    } catch (e) {
      setProblems([errMsg(e)]);
    }
    setConfirmId(null);
    await refresh();
  }

  const uploading = Object.entries(uploads);
  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Document library</h1>
        <p className="mt-1 text-sm text-muted">
          Upload papers, datasheets and reports. Attach ready documents to a simulation so the research cites them.
        </p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files); }}
        className={`rounded-lg border-2 border-dashed p-8 text-center ${dragging ? "border-accent bg-accent/10" : "border-line bg-surface"}`}
      >
        <p className="text-sm">Drag files here, or</p>
        <input
          ref={input}
          id="file-input"
          type="file"
          multiple
          accept={ACCEPT_ATTR}
          className="sr-only"
          onChange={(e) => { if (e.target.files) void upload(e.target.files); e.target.value = ""; }}
        />
        <button
          type="button"
          onClick={() => input.current?.click()}
          className="mt-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Choose files
        </button>
        <p className="mt-2 text-xs text-muted">PDF, DOCX, PPTX, XLSX, CSV, HTML, Markdown, text, PNG, JPEG, WebP. Up to 50 MB each.</p>
      </div>

      {uploading.length > 0 ? (
        <ul className="space-y-1 text-sm" aria-label="Uploads in progress">
          {uploading.map(([k, u]) => (
            <li key={k} className="flex items-center gap-2">
              <span className="truncate">{u.name}</span>
              <progress className="h-2 w-40" value={u.p} max={1} aria-label={`Upload progress for ${u.name}`} />
              <span className="text-xs text-muted">{Math.round(u.p * 100)}%</span>
            </li>
          ))}
        </ul>
      ) : null}
      {problems.length > 0 ? (
        <ul role="alert" className="space-y-1 text-sm text-bad">{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
      ) : null}
      {error ? <p role="alert" className="rounded-lg border border-bad/50 bg-bad/10 p-3 text-sm">{error}</p> : null}
      <p className="sr-only" aria-live="polite">{announce}</p>

      {docs === null ? <p className="text-sm text-muted">Loading…</p> : null}
      {docs && docs.length === 0 && !error ? <p className="text-sm text-muted">No documents yet.</p> : null}
      <ul className="space-y-3" aria-live="polite">
        {docs?.map((d) => (
          <li
            key={d.id}
            id={`doc-${d.id}`}
            className={`rounded-lg border bg-surface p-4 ${highlight === d.id ? "border-accent ring-2 ring-accent" : "border-line"}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium">{d.title || d.filename}</div>
                {d.title && d.title !== d.filename ? <div className="truncate text-xs text-muted">{d.filename}</div> : null}
              </div>
              <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${BADGE[d.status]}`}>{d.status}</span>
            </div>
            {d.status === "ready" ? (
              <p className="mt-2 text-xs text-muted">
                {d.pages} pages · {d.n_chunks} chunks · {d.n_figures} figures · {d.n_tables} tables
              </p>
            ) : null}
            {d.status === "failed" && d.error ? <p className="mt-2 break-words text-sm text-bad">{d.error}</p> : null}
            <div className="mt-2">
              {confirmId === d.id ? (
                <span role="group" aria-label={`Confirm deleting ${d.title || d.filename}`} className="flex flex-wrap items-center gap-2 text-sm">
                  <span>Delete this document and its index?</span>
                  <button autoFocus onClick={() => void remove(d.id)} className="rounded-md bg-bad px-3 py-1 text-xs font-semibold text-white">Delete</button>
                  <button onClick={() => setConfirmId(null)} className="rounded-md border border-line px-3 py-1 text-xs">Cancel</button>
                </span>
              ) : (
                <button onClick={() => setConfirmId(d.id)} className="text-sm text-bad underline underline-offset-2" aria-label={`Delete ${d.title || d.filename}`}>
                  Delete
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
