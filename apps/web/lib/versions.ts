// Helpers for the version strip: each follow-up edit is a job of its own, listed by GET /jobs/{id}/versions.
import type { Stage, VersionInfo } from "./types";

/** Short present-tense words for a version that is still being built. */
const STAGE_WORDS: Record<Stage, string> = {
  interpreting_inputs: "reading your notes",
  planning_edit: "planning the change",
  clarifying: "preparing questions",
  awaiting_clarification: "needs your answers",
  researching: "researching",
  modeling: "modelling",
  coding: "writing the solver",
  running: "running",
  evaluating: "checking",
  awaiting_asset_picks: "pick the 3D models",
  packaging: "building the 3D scene",
  awaiting_review: "ready to review",
  approved: "accepted",
  failed: "failed",
};

export const stageWords = (stage: Stage): string => STAGE_WORDS[stage];

/** Versions settle once accepted or failed; anything else is still moving (or waiting for the user). */
export const isSettled = (stage: Stage): boolean => stage === "approved" || stage === "failed";

export const hasPending = (versions: VersionInfo[]): boolean => versions.some((v) => !isSettled(v.stage));

export interface Chip {
  jobId: string;
  label: string;
  current: boolean;
  live: boolean;
  /** Words for a version that is still being built; null once settled. */
  progress: string | null;
  failed: boolean;
  title: string;
}

/** Chips for the strip, ordered by version; nothing to show for a job with a single version. */
export function versionChips(versions: VersionInfo[], currentJobId: string): Chip[] {
  if (versions.length < 2) return [];
  return [...versions].sort((a, b) => a.version - b.version).map((v) => ({
    jobId: v.job_id,
    label: `v${v.version}`,
    current: v.job_id === currentJobId,
    live: v.live,
    progress: isSettled(v.stage) ? null : stageWords(v.stage),
    failed: v.stage === "failed",
    title: v.request ? `${v.request}${v.summary ? `: ${v.summary}` : ""}` : "The first version",
  }));
}
