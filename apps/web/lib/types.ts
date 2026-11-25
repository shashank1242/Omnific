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
export interface Entity { name: string; kind: string; bound_state: string[]; properties?: string[] }
export interface TimeSpan {
  start: number; end: number; unit: string; output_samples: number;
  /** Absent in specs from before stop rules existed (they behaved as "fixed"). */
  stop?: "settle" | "fixed"; settle_window_s?: number; stop_reason?: string;
}
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
  /** Absent in specs from before environments existed. */
  environment?: (PlaybackEnvironment & { parameters: string[] }) | null;
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
  | "interpreting_inputs" | "clarifying" | "awaiting_clarification" | "researching" | "modeling" | "coding" | "running" | "evaluating" | "packaging"
  | "awaiting_review" | "approved" | "failed";

export type ClarifyKind = "choice" | "number" | "boolean" | "text";
export interface ClarifyQuestion {
  id: string; question: string; why: string; kind: ClarifyKind; options: string[]; unit: string; default: string;
}
export interface ClarifyAnswer { id: string; value: string; custom?: boolean }
export interface ClarifyAnswers { answers: ClarifyAnswer[]; use_defaults: boolean }
export interface Clarification { question: string; answer: string; unit: string; used_default: boolean; custom?: boolean }

// Mesh v2 scene report (services/workflows SceneBuild)
export interface StageEval { stage: string; attempt: number; passed: boolean; checks: CheckResult[]; critique: string }
export type MeshRoute = "library" | "cad" | "generated" | "procedural";
/** Credit for a reused library model (CC0 / CC-BY). */
export interface Attribution { title: string; author: string; license: string; url: string; source: string }
export interface EntityReport {
  entity: string;
  final: "generated_rigged" | "generated" | "library_rigged" | "library" | "cad_rigged" | "cad" | "procedural";
  attempts: number; evals: StageEval[]; notes: string[];
  // mesh router (absent in reports from before it existed)
  routes_tried?: MeshRoute[]; route_used?: MeshRoute; route_reason?: string;
  attribution?: Attribution | null; step_key?: string;
}
export interface SceneReport { backend: "procedural" | "generated"; entities: EntityReport[]; scene_checks: CheckResult[] }

export type InputKind = "image" | "audio";
/** What the intake read from one attached sketch/photo or voice note. */
export interface IntakeItem { input_id: string; kind: InputKind; filename: string; text: string }

export type DocumentStatus = "pending" | "ingesting" | "ready" | "failed";
export interface LibraryDocument {
  id: string; filename: string; content_type: string; title: string; pages: number;
  status: DocumentStatus; error: string | null; n_chunks: number; n_figures: number; n_tables: number;
  created_at: string;
}

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
  /** Present while awaiting_clarification. */
  questions?: ClarifyQuestion[];
  clarifications?: Clarification[];
  /** The typed prompt plus the attachments' text; empty when the job had no attachments. */
  effective_prompt?: string;
  intake?: IntakeItem[];
  scene_report?: SceneReport | null;
}

export type JobState =
  | "running" | "completed" | "failed" | "canceled" | "terminated" | "timed_out" | "other";
export interface JobView {
  job_id: string;
  state: JobState;
  status: JobStatus | null;
  draft: JobOutcome | null;
  result: JobOutcome | null;
  /** Questions while awaiting_clarification (GET /jobs/{id}). */
  questions?: ClarifyQuestion[] | null;
}

/** Re-running a simulation with values the user changed (POST /jobs/{id}/variants). */
export interface ValueChange { name: string; value: number }
export interface VariantChanges { parameters: ValueChange[]; initial: ValueChange[]; end?: number | null }
export interface VariantRecord {
  variant_id: string; created_at: string; changes: VariantChanges;
  status: "running" | "ready" | "failed"; error: string; playback_key: string | null;
}

/** A simulation in the public library (GET /gallery). */
export interface PublishedSimulation {
  job_id: string; title: string; description: string; prompt: string; fidelity: Fidelity; domains: Domain[];
  has_thumbnail: boolean; published_at: string | null;
}
export interface PublishRequest { title?: string; description?: string; thumbnail?: string }

export interface CreateJobBody {
  prompt: string;
  max_revisions?: number;
  max_review_rounds?: number;
  document_ids?: string[];
  input_ids?: string[];
  clarify?: boolean;
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
/** How a run ended: `settle` runs stop once no state has changed for `window_s` (plus that buffer). */
/** Where the simulation happens, as the viewer draws it (playback-format.md `environment`). */
export interface PlaybackEnvironment {
  name: string; description: string; kind: "outdoor" | "indoor" | "space" | "underwater" | "planetary";
  ground: "flat" | "none"; ground_material: string; lighting: "day" | "dusk" | "night" | "studio" | "space";
  sky_color: string; ground_color: string; fog: number;
}
export interface PlaybackStop { rule: "settle" | "fixed"; at: number; window_s: number; settled: boolean; reason: string }
interface PlaybackBase {
  title: string;
  fidelity: Fidelity;
  t: number[];
  unit_t: string;
  series: Record<string, Series>;
  entities: PlaybackEntity[];
  tracks: Track[];
  stop?: PlaybackStop;
  environment?: PlaybackEnvironment;
}
export interface PlaybackV1 extends PlaybackBase { version: 1 }
export interface PlaybackV2 extends PlaybackBase { version: 2; length_scale: number }
export type Playback = PlaybackV1 | PlaybackV2;

export interface DemoIndexEntry { slug: string; title: string; description: string; fidelity?: Fidelity }
