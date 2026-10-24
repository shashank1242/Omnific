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


ACTIVITY = {
    TaskQueue.RESEARCHER: "research",
    TaskQueue.MODELER: "build_spec",
    TaskQueue.SOLVER_CODER: "write_solver",
    TaskQueue.SANDBOX: "run_solver",
    TaskQueue.DATA: "prepare_playback",
    TaskQueue.MESH: "build_scene",
    TaskQueue.EVALUATOR: "evaluate",
}


class JobRequest(BaseModel):
    job_id: str
    prompt: str
    max_revisions: int = Field(default=3, ge=0, le=10, description="Automatic revisions per round")
    max_review_rounds: int = Field(default=3, ge=1, le=10, description="Human reject/redo rounds")
    review_timeout_hours: float = Field(default=72, gt=0)


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
    "researching", "modeling", "coding", "running", "evaluating", "packaging",
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
    spec: SimSpec | None = None
    research: ResearchOutput | None = None
    eval_report: EvalReport | None = None
    code_key: str | None = None
    result_key: str | None = None
    playback_key: str | None = None
    scene_key: str | None = None
    failure_reason: str = ""
