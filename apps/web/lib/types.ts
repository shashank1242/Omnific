// Mirrors packages/omnific-common contracts.py and omnific-sim spec.py (JSON as served by the API).

export type Fidelity = "analytic" | "validated_numerical" | "heuristic";
export type Domain =
  | "mechanics" | "thermal" | "chemistry" | "fluids" | "electromagnetism" | "mathematics" | "other";
export type Blame = "modeler" | "solver_coder";

export interface Citation { title: string; url: string; note: string }
export interface Parameter {
  name: string; value: number; unit: string; description: string; source: Citation | null;
}
export interface StateVariable { name: string; unit: string; initial_value: number; description: string }
export interface Equation { label: string; expression: string; description: string }
export interface Entity { name: string; kind: string; bound_state: string[] }
export interface TimeSpan { start: number; end: number; unit: string; output_samples: number }
export interface SimSpec {
  title: string;
  prompt: string;
  domains: Domain[];
  fidelity: Fidelity;
  assumptions: string[];
  parameters: Parameter[];
  state: StateVariable[];
  equations: Equation[];
  entities: Entity[];
  time: TimeSpan;
  invariants: { name: string; expression: string; rel_tolerance: number }[];
  bounds: { variable: string; min: number | null; max: number | null }[];
  analytic_checks: unknown[];
  citations: Citation[];
}

export interface Fact { name: string; value: number; unit: string; description: string; citation: Citation }
export interface ResearchOutput {
  summary: string; phenomena: string[]; facts: Fact[]; open_questions: string[];
}
export interface CheckResult { name: string; passed: boolean; detail: string; blame: Blame | null }
export interface EvalReport { passed: boolean; checks: CheckResult[]; critic_summary: string }

export type Stage =
  | "researching" | "modeling" | "coding" | "running" | "evaluating" | "packaging"
  | "awaiting_review" | "approved" | "failed";

export interface ReviewDecision { approved: boolean; reviewer: string; notes: string; blame: Blame | null }
export interface JobStatus { job_id: string; stage: Stage; revision: number; message: string }
export interface JobOutcome {
  job_id: string;
  status: "awaiting_review" | "approved" | "failed";
  revisions: number;
  review_round: number;
  reviews: ReviewDecision[];
  spec: SimSpec | null;
  research: ResearchOutput | null;
  eval_report: EvalReport | null;
  code_key: string | null;
  result_key: string | null;
  playback_key: string | null;
  scene_key: string | null;
  failure_reason: string;
}

export type JobState =
  | "running" | "completed" | "failed" | "canceled" | "terminated" | "timed_out" | "other";
export interface JobView {
  job_id: string;
  state: JobState;
  status: JobStatus | null;
  draft: JobOutcome | null;
  result: JobOutcome | null;
}

export interface CreateJobBody {
  prompt: string;
  max_revisions?: number;
  max_review_rounds?: number;
}

// --- playback.json v1/v2 (docs/playback-format.md) ---
export type Channel =
  | "position_x" | "position_y" | "position_z" | "rotation_z" | "scale" | "color" | "color_field";

export interface ScalarSeries { unit: string; values: number[] }
export interface FieldSeries { unit: string; values: number[][]; field_axis?: string }
export type Series = ScalarSeries | FieldSeries;

export interface Track { entity: string; channel: Channel; state: string; min: number; max: number; scale: number }
export type Vec3 = [number, number, number];
export interface PlaybackEntity {
  name: string; kind: string; bound_state: string[];
  /** v2 articulation: parent entity name (null = fixed pivot), pivot (null = origin), arm (presence = articulated). */
  parent?: string | null;
  pivot?: Vec3 | null;
  arm?: Vec3;
}
interface PlaybackBase {
  title: string;
  fidelity: Fidelity;
  t: number[];
  unit_t: string;
  series: Record<string, Series>;
  entities: PlaybackEntity[];
  tracks: Track[];
}
export interface PlaybackV1 extends PlaybackBase { version: 1 }
export interface PlaybackV2 extends PlaybackBase { version: 2; length_scale: number }
export type Playback = PlaybackV1 | PlaybackV2;

export interface DemoIndexEntry { slug: string; title: string; description: string }
