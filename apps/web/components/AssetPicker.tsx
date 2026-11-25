"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError, choiceThumbUrl, submitPicks } from "@/lib/api";
import { ROUTE_LABEL, badge, buildPicks, initialSelection, optionId, reconcile, routesFor, type Selection } from "@/lib/picker";
import type { AssetChoices, EntityChoices } from "@/lib/types";
import Icon from "./Icon";

const TONE = { ok: "text-ok", bad: "text-bad", muted: "text-muted" } as const;

/**
 * Choose each object's 3D model while the simulation builds. Every option already passed the licence filter
 * (CC0 / CC-BY) and was ranked by the vision shortlist; the router's own picks are fully evaluated in the
 * background and their badges update live. Nothing chosen = Omnific uses the best option that passes its checks.
 */
export default function AssetPicker({ jobId, choices, waiting, onDone }: {
  jobId: string; choices: AssetChoices; waiting: boolean; onDone: () => void;
}) {
  const [sel, setSel] = useState<Record<string, Selection>>(() => initialSelection(choices));
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // options update while the evaluations run: keep the selection valid (a chosen model may fail its checks)
  const valid = useMemo(() => reconcile(choices, sel), [choices, sel]);
  useEffect(() => {
    if (JSON.stringify(valid) !== JSON.stringify(sel)) setSel(valid);
  }, [valid, sel]);

  async function send(useAuto: boolean) {
    setBusy(true);
    setError(null);
    try {
      await submitPicks(jobId, useAuto ? { picks: [] } : buildPicks(choices, valid));
      setSent(true);
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setSent(true);
        setError("The 3D scene is already being built with Omnific's own choices.");
        onDone();
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <p role="status" className="rounded-lg border border-line bg-surface p-3 text-sm">
        {error ?? "Your choices are in. They are used when the 3D scene is built."}
      </p>
    );
  }

  const set = (entity: string, s: Selection) => setSel((cur) => ({ ...cur, [entity]: s }));

  return (
    <form
      aria-labelledby="picker-title"
      onSubmit={(e) => { e.preventDefault(); void send(false); }}
      className="space-y-4 rounded-lg border border-accent bg-surface p-4"
    >
      <div>
        <h2 id="picker-title" className="text-lg font-semibold">Choose the 3D models</h2>
        <p className="text-sm text-muted">
          While the simulation builds, pick how each object should look. Every model shown is openly licensed (CC0 or
          CC-BY, credited in the scene). {waiting
            ? "The build is waiting for you; if you don't choose, Omnific uses the best option that passes its checks."
            : "No rush: the build continues meanwhile."}
        </p>
      </div>
      {choices.entities.map((e) => (
        <EntityCard key={e.entity} jobId={jobId} e={e} sel={valid[e.entity] ?? { route: "auto", candidateId: "" }}
          onChange={(s) => set(e.entity, s)} />
      ))}
      {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className="h-10 rounded-lg bg-flash px-5 text-sm font-bold text-flash-fg disabled:opacity-50">
          Use these models
        </button>
        <button type="button" disabled={busy} onClick={() => void send(true)} className="h-10 rounded-md border border-line px-5 text-sm disabled:opacity-50">
          Let Omnific choose all
        </button>
      </div>
    </form>
  );
}

function EntityCard({ jobId, e, sel, onChange }: {
  jobId: string; e: EntityChoices; sel: Selection; onChange: (s: Selection) => void;
}) {
  const routes = routesFor(e);
  const name = `type-${e.entity}`;
  return (
    <fieldset className="rounded-md border border-line p-3">
      <legend className="px-1 text-sm font-semibold">
        {e.entity} <span className="font-normal text-muted">({e.kind.replace(/_/g, " ")})</span>
      </legend>
      <p className="text-xs text-muted">
        Omnific would use: {ROUTE_LABEL[e.default_route]}{e.query ? <> · searched for &ldquo;{e.query}&rdquo;</> : null}
        {e.note ? <> · {e.note}</> : null}
      </p>
      <div role="radiogroup" aria-label={`How ${e.entity} is made`} className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {routes.map((r) => (
          <label key={r} className="flex items-center gap-1.5 text-sm">
            <input type="radio" name={name} value={r} checked={sel.route === r}
              onChange={() => onChange({ route: r, candidateId: r === "library" ? (sel.candidateId || firstOk(e)) : "" })} />
            {ROUTE_LABEL[r]}
          </label>
        ))}
      </div>
      {e.options.length && routes.includes("library") ? (
        <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label={`Models for ${e.entity}`}>
          {e.options.map((o, i) => {
            const id = optionId(o);
            const b = badge(o, e.default_route);
            const chosen = sel.route === "library" && sel.candidateId === id;
            const disabled = o.status === "failed";
            return (
              <li key={id}>
                <label className={`block h-full cursor-pointer rounded-md border p-2 text-xs transition-colors focus-within:outline focus-within:outline-2 focus-within:outline-accent ${
                  chosen ? "border-accent bg-accent/10" : "border-line hover:border-fg/40"} ${disabled ? "cursor-not-allowed opacity-60" : ""}`}>
                  <input type="radio" className="sr-only" name={`model-${e.entity}`} value={id} checked={chosen}
                    disabled={disabled} onChange={() => onChange({ route: "library", candidateId: id })} />
                  {o.thumb_key ? (
                    // eslint-disable-next-line @next/next/no-img-element -- small thumbnails served by our API
                    <img src={choiceThumbUrl(jobId, e.entity, i)} alt={o.candidate.name} loading="lazy"
                      className="mb-1 aspect-square w-full rounded bg-bg object-contain" />
                  ) : (
                    <div className="mb-1 grid aspect-square w-full place-items-center rounded bg-bg text-muted">no preview</div>
                  )}
                  <span className="line-clamp-2 font-medium text-sm">{o.candidate.name}</span>
                  <span className="block text-muted">
                    by {o.candidate.author || "unknown"} · {o.candidate.license} · {o.candidate.source}
                  </span>
                  <span className="block text-muted">
                    match <span className="font-mono text-data">{o.score}/10</span>
                    {o.candidate.tris ? <> · <span className="font-mono">{o.candidate.tris.toLocaleString()}</span> tris</> : null}
                  </span>
                  <span className={`mt-1 flex items-start gap-1 ${TONE[b.tone]}`}>
                    {b.tone === "ok" ? <Icon name="check" size={14} /> : b.tone === "bad" ? <Icon name="x" size={14} /> : null}
                    <span className="line-clamp-3">{b.text}</span>
                  </span>
                  <a href={o.candidate.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-accent underline">
                    View source
                  </a>
                </label>
              </li>
            );
          })}
        </ul>
      ) : null}
    </fieldset>
  );
}

function firstOk(e: EntityChoices): string {
  const o = e.options.find((x) => x.status === "passed") ?? e.options.find((x) => x.status !== "failed");
  return o ? optionId(o) : "";
}
