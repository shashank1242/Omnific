"""Activity inputs and outputs for the SimulationJob workflow.

Every agent is a Temporal worker on its own task queue that registers exactly the activity named in
ACTIVITY. The workflow calls activities by name, so it never imports agent code. Large payloads
(results, scenes, playback) go to the ArtifactStore; activities pass storage keys, not bytes.
"""

from enum import Enum
from typing import Literal

from omnific_sim.spec import Citation, SimSpec
from pydantic import BaseModel, Field


class TaskQueue(str, Enum):
    WORKFLOW = "workflow"
    RESEARCHER = "researcher"
    MODELER = "modeler"
    SOLVER_CODER = "solver-coder"
    SANDBOX = "sandbox"
    DATA = "data"
    MESH = "mesh"
    EVALUATOR = "evaluator"
    CLARIFIER = "clarifier"
    LIBRARIAN = "librarian"
    INTAKE = "intake"
    MESH_GPU = "mesh-gpu"


ACTIVITY = {
    TaskQueue.RESEARCHER: "research",
    TaskQueue.MODELER: "build_spec",
    TaskQueue.SOLVER_CODER: "write_solver",
    TaskQueue.SANDBOX: "run_solver",
    TaskQueue.DATA: "prepare_playback",
    TaskQueue.MESH: "build_scene",
    TaskQueue.EVALUATOR: "evaluate",
    TaskQueue.CLARIFIER: "clarify",
    TaskQueue.LIBRARIAN: "ingest_document",
    TaskQueue.INTAKE: "interpret_inputs",
}


class SceneActivity(str, Enum):
    """SceneBuild (mesh v2) runs several activities per queue; each name says which queue serves it."""

    PLAN = "plan_assets"                    # mesh (CPU)
    PROCEDURAL = "procedural_mesh"          # mesh
    POSTPROCESS = "postprocess_mesh"        # mesh
    PHYSICS_RIG = "physics_rig"             # mesh
    PROCEDURAL_ANIMATE = "procedural_animate"  # mesh
    EVALUATE = "evaluate_stage"             # mesh: evaluates GPU stage outputs (deterministic + vision critic)
    ASSEMBLE = "assemble_scene"             # mesh
    CACHE = "cache_asset"                   # mesh: copy a finished generated asset into the cross-job cache
    GENERATE = "generate_mesh"              # mesh-gpu
    ML_RIG = "ml_rig"                       # mesh-gpu
    ML_ANIMATE = "ml_animate"               # mesh-gpu


GPU_SCENE_ACTIVITIES = {SceneActivity.GENERATE, SceneActivity.ML_RIG, SceneActivity.ML_ANIMATE}


class InputRef(BaseModel):
    """A sketch, photo or voice note attached to a job as part of the prompt (not a library document)."""

    input_id: str
    key: str = Field(description="ArtifactStore key of the uploaded file")
    kind: Literal["image", "audio"]
    mime: str
    filename: str


class JobRequest(BaseModel):
    job_id: str
    prompt: str
    max_revisions: int = Field(default=3, ge=0, le=10, description="Automatic revisions per round")
    max_review_rounds: int = Field(default=3, ge=1, le=10, description="Human reject/redo rounds")
    review_timeout_hours: float = Field(default=72, gt=0)
    document_ids: list[str] = Field(default_factory=list, description="Library documents the agents may search")
    clarify: bool = Field(default=True, description="Ask the user clarifying questions before research")
    clarify_timeout_hours: float = Field(default=24, gt=0, description="Then proceed with the defaults")
    mesh_backend: Literal["procedural", "generated"] = Field(
        default="procedural", description="generated = 3D generation, rigging and animation on the GPU worker"
    )
    inputs: list[InputRef] = Field(default_factory=list, description="Interpreted into the prompt by the intake")


# --- Intake ---------------------------------------------------------------------------------

class IntakeInput(BaseModel):
    job_id: str
    prompt: str
    inputs: list[InputRef]


class IntakeItem(BaseModel):
    input_id: str
    kind: Literal["image", "audio"]
    filename: str
    text: str = Field(description="Image description or audio transcript")


class IntakeOutput(BaseModel):
    effective_prompt: str = Field(description="The typed prompt plus what the attachments say; used by every agent")
    items: list[IntakeItem]
    warnings: list[str] = Field(default_factory=list)


# --- Clarifier ------------------------------------------------------------------------------

class ClarifyQuestion(BaseModel):
    id: str = Field(description="Short stable identifier, e.g. 'altitude'")
    question: str
    why: str = Field(description="What changes in the simulation depending on the answer")
    kind: Literal["choice", "number", "boolean", "text"]
    options: list[str] = Field(description="Choices for kind=choice; empty otherwise")
    unit: str = Field(description="pint unit for kind=number; empty otherwise")
    default: str = Field(description="The value used if the user skips: the most standard, defensible choice")


class ClarifyInput(BaseModel):
    job_id: str
    prompt: str
    document_titles: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list, description="Searched so documents' answers aren't asked")


class ClarifyOutput(BaseModel):
    questions: list[ClarifyQuestion] = Field(description="0-6 questions; empty if the prompt is unambiguous")


class ClarifyAnswer(BaseModel):
    id: str
    value: str


class ClarifyAnswers(BaseModel):
    """Sent to the workflow as the `clarify` signal. Unanswered questions take their default."""

    answers: list[ClarifyAnswer] = Field(default_factory=list)
    use_defaults: bool = False


class Clarification(BaseModel):
    """Precedence for the agents: an explicit user answer (used_default=False) beats attached documents, which beat
    a default (used_default=True). A default is a fallback, never a reason to contradict the documents."""

    question: str
    answer: str
    unit: str = ""
    used_default: bool


# --- Librarian (RAG ingestion) --------------------------------------------------------------

class IngestInput(BaseModel):
    document_id: str
    filename: str
    content_type: str
    raw_key: str = Field(description="ArtifactStore key of the uploaded file")


class IngestOutput(BaseModel):
    document_id: str
    title: str
    pages: int
    n_chunks: int
    n_figures: int
    n_tables: int
    warnings: list[str] = Field(default_factory=list)


# --- Researcher -----------------------------------------------------------------------------

class Fact(BaseModel):
    name: str
    value: float
    unit: str
    description: str
    citation: Citation


class ResearchInput(BaseModel):
    job_id: str
    prompt: str
    clarifications: list[Clarification] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    input_filenames: list[str] = Field(
        default_factory=list, description="Interpreted attachments; their values are cited omnific://input/<filename>"
    )


class ResearchOutput(BaseModel):
    summary: str
    phenomena: list[str] = Field(description="Physical/chemical processes involved, e.g. 'radiative heating'")
    facts: list[Fact]
    open_questions: list[str] = Field(default_factory=list)


# --- Modeler --------------------------------------------------------------------------------

class ModelInput(BaseModel):
    job_id: str
    prompt: str
    research: ResearchOutput
    clarifications: list[Clarification] = Field(
        default_factory=list, description="User answers: binding. Defaults used must be stated as assumptions"
    )
    feedback: list[str] = Field(default_factory=list, description="Evaluator or reviewer notes to fix")


# Modeler output is omnific_sim.spec.SimSpec.


# --- Solver-coder ---------------------------------------------------------------------------

class SolverCodeInput(BaseModel):
    job_id: str
    spec: SimSpec
    feedback: list[str] = Field(default_factory=list)
    previous_code: str | None = None


class SolverCode(BaseModel):
    code: str = Field(
        description="Python module defining `simulate(spec: SimSpec, refine: int = 1) -> SimResult`, "
        "importing only numpy, scipy, sympy, pint and omnific_sim"
    )
    notes: str = ""


# --- Sandbox --------------------------------------------------------------------------------

class RunInput(BaseModel):
    job_id: str
    attempt: int = Field(description="Unique per job across review rounds; used in artifact keys")
    spec: SimSpec
    code: SolverCode
    refine: int = Field(
        default=1, ge=1,
        description="Passed to simulate(spec, refine=...). >1 asks for a finer timestep/mesh/tolerance "
        "for the convergence check; artifacts go under attempt-<n>-refine<r>/",
    )
    timeout_s: int = 300


class RunOutput(BaseModel):
    ok: bool
    result_key: str | None = None
    code_key: str
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0


# --- Data -----------------------------------------------------------------------------------

class PlaybackInput(BaseModel):
    job_id: str
    spec: SimSpec
    result_key: str


class PlaybackOutput(BaseModel):
    playback_key: str
    n_frames: int


# --- Mesh -----------------------------------------------------------------------------------

class SceneInput(BaseModel):
    job_id: str
    spec: SimSpec


class SceneOutput(BaseModel):
    scene_key: str
    entity_assets: dict[str, str] = Field(description="entity name -> asset id used")
    report: "SceneReport | None" = None


# --- Evaluator ------------------------------------------------------------------------------

class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str
    blame: Literal["modeler", "solver_coder"] | None = Field(
        default=None, description="Which agent should fix a failure"
    )


class EvalInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    code: SolverCode
    result_key: str
    fine_result_key: str | None = Field(
        default=None, description="Same solver run with refine=2, for the convergence check"
    )


class EvalReport(BaseModel):
    passed: bool
    checks: list[CheckResult]
    critic_summary: str = ""


# --- Scene build (mesh v2; docs/architecture.md) -----------------------------------------------

MeshBackend = Literal["procedural", "generated"]
MeshStage = Literal["plan", "generate", "postprocess", "physics_rig", "ml_rig", "animate", "assemble"]


class ClipPlan(BaseModel):
    name: str = Field(description="Short identifier, e.g. 'walk'")
    kind: Literal["idle", "locomotion", "flex", "custom"]
    description: str = Field(description="What the motion looks like, for the animator and the critic")
    drive_state: str = Field(
        description="State variable whose |value| scales playback rate (e.g. speed 'v'); empty for a constant rate"
    )


class AssetPlan(BaseModel):
    entity: str
    kind: str
    mode: Literal["procedural", "generated"]
    gen_prompt: str = Field(description="Visual description for 3D generation; empty for procedural")
    rig: Literal["none", "physics", "ml", "both"]
    clips: list[ClipPlan] = Field(description="Secondary-motion clips; empty unless the body has moving parts")
    poly_budget: int = Field(description="Max triangles after decimation")


class AssetPlanOutput(BaseModel):
    assets: list[AssetPlan]


class PlanAssetsInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    backend: MeshBackend = "procedural"


class PlanAssetsOutput(BaseModel):
    assets: list["AssetPlan"]
    eval: "StageEval"
    cached: list["MeshArtifact"] = Field(default_factory=list, description="Finished assets found in the cache")


class MeshArtifact(BaseModel):
    """One entity's asset at some stage. Procedural assets carry no GLB: the scene builds them from `asset`."""

    entity: str
    source: Literal["procedural", "generated"]
    asset: str = Field(description="Procedural asset id the entity maps to (also the size reference)")
    glb_key: str = Field(default="", description="ArtifactStore key of the GLB for generated assets")
    n_tris: int = 0
    bbox_min: list[float] = Field(default_factory=list)
    bbox_max: list[float] = Field(default_factory=list)
    bones: list[str] = Field(default_factory=list)
    clips: list[ClipPlan] = Field(default_factory=list)
    joint: list[float] = Field(
        default_factory=list, description="Physics joint: [pivot x, y, z, arm x, y, z] in scene units; empty if none"
    )


class StageEval(BaseModel):
    stage: MeshStage
    attempt: int
    passed: bool
    checks: list[CheckResult]
    critique: str = Field(default="", description="Fed back into the next attempt")


class MeshStageInput(BaseModel):
    job_id: str
    plan: AssetPlan
    spec: SimSpec
    artifact: MeshArtifact | None = None
    attempt: int = 0
    critique: str = ""


class MeshStageOutput(BaseModel):
    artifact: MeshArtifact
    eval: StageEval


class EvaluateStageInput(BaseModel):
    job_id: str
    stage: MeshStage
    plan: AssetPlan
    artifact: MeshArtifact
    attempt: int = 0
    before: MeshArtifact | None = Field(default=None, description="Input of the stage, for comparisons")


class EntityReport(BaseModel):
    entity: str
    final: Literal["generated_rigged", "generated", "procedural"]
    attempts: int
    evals: list[StageEval]
    notes: list[str] = Field(default_factory=list)


class SceneReport(BaseModel):
    backend: MeshBackend
    entities: list[EntityReport]
    scene_checks: list[CheckResult] = Field(default_factory=list)


class SceneBuildInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    backend: MeshBackend = "procedural"
    max_attempts: int = Field(default=3, ge=1, le=10)
    gpu_wait_minutes: float = Field(
        default=15.0, gt=0, description="How long a GPU stage may wait for a GPU worker before falling back"
    )


class AssembleInput(BaseModel):
    job_id: str
    spec: SimSpec
    artifacts: list[MeshArtifact]


class AssembleOutput(BaseModel):
    scene_key: str
    entity_assets: dict[str, str]
    eval: StageEval


# --- Workflow state -------------------------------------------------------------------------

Stage = Literal[
    "interpreting_inputs",
    "clarifying", "awaiting_clarification", "researching", "modeling", "coding", "running", "evaluating", "packaging",
    "awaiting_review", "approved", "failed",
]


class ReviewDecision(BaseModel):
    """Sent to the workflow as the `review` signal."""

    approved: bool
    reviewer: str
    notes: str = ""
    blame: Literal["modeler", "solver_coder"] | None = Field(
        default=None, description="On reject: which stage to redo (default: modeler, i.e. redo the model)"
    )


class JobStatus(BaseModel):
    job_id: str
    stage: Stage
    revision: int = 0
    message: str = ""


class JobOutcome(BaseModel):
    """Final workflow result; while awaiting review the same shape is exposed as the `draft` query."""

    job_id: str
    status: Literal["awaiting_review", "approved", "failed"]
    revisions: int
    effective_prompt: str = Field(default="", description="Set when attachments were interpreted into the prompt")
    intake: list[IntakeItem] = Field(default_factory=list)
    review_round: int = 0
    reviews: list[ReviewDecision] = Field(default_factory=list)
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    spec: SimSpec | None = None
    research: ResearchOutput | None = None
    eval_report: EvalReport | None = None
    scene_report: SceneReport | None = None
    code_key: str | None = None
    result_key: str | None = None
    playback_key: str | None = None
    scene_key: str | None = None
    failure_reason: str = ""


SceneOutput.model_rebuild()  # forward references
PlanAssetsOutput.model_rebuild()
