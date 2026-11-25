"use client";

import { useEffect, useRef, useState } from "react";
import { blendUrl, startVariantBlend, variantBlendStatus, variantBlendUrl } from "@/lib/api";
import Icon from "./Icon";

const POLL_MS = 1500;
const btn =
  "inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-sm text-fg transition-colors " +
  "hover:border-fg/40 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";

/**
 * "Open in Blender": the run on screen as a .blend where the same simulation plays on Space. The original run's file is
 * built with the job; a Tune run's is exported on demand (a few seconds, no AI involved).
 */
export default function BlendButton({ jobId, run, hasMainBlend }: { jobId: string; run: string; hasMainBlend: boolean }) {
  const [state, setState] = useState<"idle" | "building" | "failed">("idle");
  const timer = useRef<number | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    setState("idle");
    return () => {
      live.current = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [jobId, run]);

  const hint = <span className="text-xs text-muted">Plays on Space in Blender 4.2 or newer.</span>;
  if (run === "original") {
    if (!hasMainBlend) return null;
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <a className={btn} href={blendUrl(jobId)} download>
          <Icon name="download" size={16} /> Open in Blender (.blend)
        </a>
        {hint}
      </span>
    );
  }

  const exportRun = async () => {
    setState("building");
    try {
      await startVariantBlend(jobId, run);
      const poll = async () => {
        if (!live.current) return;
        let s: "building" | "ready" | "failed";
        try {
          s = await variantBlendStatus(jobId, run);
        } catch {
          s = "failed"; // a status request that fails (network, server) must not leave the button stuck
        }
        if (!live.current) return;
        if (s === "ready") {
          setState("idle");
          window.location.assign(variantBlendUrl(jobId, run)); // served as an attachment: downloads, stays on page
        } else if (s === "failed") {
          setState("failed");
        } else {
          timer.current = window.setTimeout(() => void poll(), POLL_MS);
        }
      };
      await poll();
    } catch {
      if (live.current) setState("failed");
    }
  };

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" className={btn} onClick={() => void exportRun()} disabled={state === "building"}
        aria-busy={state === "building"}>
        <Icon name="download" size={16} /> {state === "building" ? "Preparing .blend…" : "Open this run in Blender (.blend)"}
      </button>
      {state === "failed" ? <span role="status" className="text-xs text-muted">The export failed. Try again.</span> : hint}
    </span>
  );
}
