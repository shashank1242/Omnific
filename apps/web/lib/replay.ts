// Demo replay: a prompt that matches a published simulation opens that finished job (POST /jobs answers
// `replayed: true`, no AI runs). The job page first plays the build diagram through its stages, as a real build
// would, then shows the simulation. The mark lives in sessionStorage, so a reload or a shared link opens it directly.
import type { Stage } from "./types";

/** Each stage and how long it shows (seconds): about 35 s in all. */
export const REPLAY_STAGES: readonly { stage: Stage; seconds: number }[] = [
  { stage: "clarifying", seconds: 2.5 },
  { stage: "researching", seconds: 6 },
  { stage: "modeling", seconds: 5 },
  { stage: "coding", seconds: 4.5 },
  { stage: "running", seconds: 3.5 },
  { stage: "evaluating", seconds: 5 },
  { stage: "packaging", seconds: 8 },
];
export const REPLAY_SECONDS = REPLAY_STAGES.reduce((s, x) => s + x.seconds, 0);

const key = (jobId: string) => `omnific.replay.${jobId}`;

/** The stage to show `elapsed` seconds into a replay, or null once it is over. */
export function replayStage(elapsed: number): Stage | null {
  if (!(elapsed >= 0)) return REPLAY_STAGES[0]?.stage ?? null;
  let t = 0;
  for (const s of REPLAY_STAGES) {
    t += s.seconds;
    if (elapsed < t) return s.stage;
  }
  return null;
}

export function markReplay(jobId: string, now = Date.now()) {
  try { sessionStorage.setItem(key(jobId), String(now)); } catch { /* storage blocked: the result opens directly */ }
}

/** When the replay of this job started (ms), or null when there is none to play. */
export function replayStart(jobId: string): number | null {
  try {
    const v = Number(sessionStorage.getItem(key(jobId)));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

export function clearReplay(jobId: string) {
  try { sessionStorage.removeItem(key(jobId)); } catch { /* nothing to clear */ }
}
