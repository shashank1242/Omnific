// What the build-progress diagram shows: the pipeline's architecture as user-facing steps. Steps stay unlit until
// reached; each finished step fires its gel (components/BuildProgress), so the diagram fills with colour.
import type { Stage } from "./types";

export type StepId = "idea" | "clarify" | "research" | "model" | "code" | "run" | "check" | "scene" | "ready";
export type StepState = "done" | "current" | "todo" | "failed";

export interface Step {
  id: StepId;
  label: string;
  detail: string;
  /** Inside the self-correcting build loop (model → code → run → check, repeated until the checks pass). */
  loop: boolean;
}

export const STEPS: Step[] = [
  { id: "idea", label: "Your idea", detail: "Reading your prompt and any sketches or voice notes", loop: false },
  { id: "clarify", label: "Questions", detail: "Anything we should ask you first", loop: false },
  { id: "research", label: "Research", detail: "Physics and reliable values, with sources", loop: false },
  { id: "model", label: "Model", detail: "The equations behind your scenario", loop: true },
  { id: "code", label: "Solver", detail: "Code that solves the equations", loop: true },
  { id: "run", label: "Run", detail: "Running the simulation", loop: true },
  { id: "check", label: "Check", detail: "Testing the result against physics", loop: true },
  { id: "scene", label: "3D scene", detail: "Models, motion and charts", loop: false },
  { id: "ready", label: "Ready", detail: "Yours to explore", loop: false },
];

const STEP_OF: Record<Stage, StepId> = {
  interpreting_inputs: "idea",
  clarifying: "clarify",
  awaiting_clarification: "clarify",
  researching: "research",
  modeling: "model",
  coding: "code",
  running: "run",
  evaluating: "check",
  packaging: "scene",
  awaiting_review: "ready",
  approved: "ready",
  failed: "ready",
};

const HEADLINE: Record<Stage, string> = {
  interpreting_inputs: "Reading your sketches and voice notes…",
  clarifying: "Working out whether anything needs asking…",
  awaiting_clarification: "A few quick questions for you below.",
  researching: "Researching the physics and finding reliable values…",
  modeling: "Writing the equations that describe your scenario…",
  coding: "Writing the code that solves them…",
  running: "Running the simulation…",
  evaluating: "Checking the results against the laws of physics…",
  packaging: "Building the 3D scene…",
  awaiting_review: "Your simulation is ready. Accept it, or ask for changes.",
  approved: "Your simulation is ready.",
  failed: "This simulation could not be built.",
};

export interface Progress {
  steps: (Step & { state: StepState })[];
  /** 0..1: how far along the build is (drives how colourful the diagram is). */
  fraction: number;
  headline: string;
  /** A check found a problem and the loop is fixing it. */
  looping: boolean;
}

export function buildProgress(stage: Stage | null, opts: { revision?: number; failed?: boolean } = {}): Progress {
  const failed = opts.failed === true || stage === "failed";
  const current = stage ? STEP_OF[stage] : "idea";
  const idx = STEPS.findIndex((s) => s.id === current);
  const finished = stage === "approved" || stage === "awaiting_review";
  const looping = !failed && (opts.revision ?? 0) > 0 && (STEPS[idx]?.loop ?? false);
  const steps = STEPS.map((s, i) => {
    let state: StepState = i < idx ? "done" : i === idx ? "current" : "todo";
    if (finished && i === idx) state = stage === "approved" ? "done" : "current";
    if (failed && i === idx) state = "failed";
    return { ...s, state };
  });
  const last = STEPS.length - 1;
  const fraction = finished ? 1 : Math.min(1, Math.max(0, (idx + (failed ? 0 : 0.5)) / last));
  let headline = stage ? HEADLINE[stage] : "Starting up…";
  if (looping) headline += " A check found something to fix, so it's improving the result.";
  return { steps, fraction, headline, looping };
}
