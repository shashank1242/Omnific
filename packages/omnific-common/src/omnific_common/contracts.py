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
}


class JobRequest(BaseModel):
    job_id: str
    prompt: str
    max_revisions: int = Field(default=3, ge=0, le=10, description="Automatic revisions per round")
    max_review_rounds: int = Field(default=3, ge=1, le=10, description="Human reject/redo rounds")
    review_timeout_hours: float = Field(default=72, gt=0)
    document_ids: list[str] = Field(default_factory=list, description="Library documents the agents may search")
    clarify: bool = Field(default=True, description="Ask the user clarifying questions before research")
    clarify_timeout_hours: float = Field(default=24, gt=0, description="Then proceed with the defaults")


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


# --- Workflow state -------------------------------------------------------------------------

Stage = Literal[
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
    review_round: int = 0
    reviews: list[ReviewDecision] = Field(default_factory=list)
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    spec: SimSpec | None = None
    research: ResearchOutput | None = None
    eval_report: EvalReport | None = None
    code_key: str | None = None
    result_key: str | None = None
    playback_key: str | None = None
    scene_key: str | None = None
    failure_reason: str = ""
