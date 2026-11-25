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
    DRESS = "dress"


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

# A second activity on the modeler worker: sorts a follow-up change to a finished simulation into a re-run plan.
EDIT_ACTIVITY = "classify_edit"


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
    ROUTE_STATS = "record_route_stats"      # mesh: per-kind route outcomes the router learns from
    LIBRARY_SEARCH = "library_search"       # mesh (network): search model libraries, shortlist by thumbnail
    LIBRARY_FETCH = "library_fetch"         # mesh (network): download + safety-check one library model
    LIBRARY_RIG = "library_rig"             # mesh: judge (keep or strip) a library model's own skin and clips
    CAD_WRITE = "cad_write"                 # mesh: LLM writes a build123d script (never executed in the worker)
    CAD_EVALUATE = "cad_evaluate"           # mesh: judge the sandbox's CAD output, rig it from its joints, clips
    CAD_SKIN = "cad_skin"                   # mesh: fit a generated surface onto a finished CAD model and its joints
    CHECK_SCENE = "check_scene"             # mesh: the scene.gltf contract checks on any stored scene (scene dressing)
    ASSET_CHOICES = "asset_choices"         # mesh (network): candidates the user picks from (asset picker)
    SCENERY_PROPS = "scenery_props"         # mesh (network): CC0 props for scene dressing (catalogue / fetch)


GPU_SCENE_ACTIVITIES = {SceneActivity.GENERATE, SceneActivity.ML_RIG, SceneActivity.ML_ANIMATE}
# Second activity on the sandbox queue: generated CAD code runs in the CAD runner image, like solver code.
CAD_RUN_ACTIVITY = "run_cad"
# Third activity on the sandbox queue: LLM-written Blender scripts run in the Blender runner image (scene dressing).
BLENDER_RUN_ACTIVITY = "run_blender"


class DressActivity(str, Enum):
    """SceneDress (Blender scene dressing) activities on the dress worker."""

    WRITE = "dress_write"        # MODEL_REASONING writes the Look / Scenery / Lighting scripts (never executed here)
    EVALUATE = "dress_evaluate"  # judge the Blender runner's output: deterministic checks, then the vision critic


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
    edit: "EditContext | None" = Field(
        default=None, description="Set for a follow-up version of a finished simulation: what it starts from"
    )
    dress: bool = Field(
        default=True, description="Dress the scene in Blender (materials, scenery, lights) and export a .blend"
    )
    pick_assets: bool = Field(
        default=True, description="mesh_backend=generated: let the user pick each object's model while the job builds"
    )
    pick_timeout_minutes: float = Field(
        default=30, gt=0, description="Packaging waits this long for picks, then uses the evaluator's best choice"
    )


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
    custom: bool = Field(default=False, description="The user typed their own answer instead of picking an option")


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
    custom: bool = Field(default=False, description="The user's own words (a typed answer, possibly with its own unit)")


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
    previous_spec: SimSpec | None = Field(
        default=None, description="The version the user asked to change: keep everything they didn't ask to change"
    )
    change_requests: list[str] = Field(
        default_factory=list, description="The user's requested changes to previous_spec, in their words: binding"
    )


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
    length_scale: float = Field(default=1.0, description="Scene units per metre (SceneBuild draws true sizes with it)")


# --- Variants (the user changes values and re-runs the existing solver; no LLM) ---------------

class ValueChange(BaseModel):
    name: str
    value: float = Field(description="In the unit the SimSpec already uses for this name")


class VariantChanges(BaseModel):
    """Values the user changed (lists, not maps, to stay strict-JSON-schema compatible)."""

    parameters: list[ValueChange] = Field(default_factory=list, description="New parameter values")
    initial: list[ValueChange] = Field(default_factory=list, description="New initial values of state variables")
    end: float | None = Field(default=None, description="New end of the time span")


class VariantInput(BaseModel):
    job_id: str = Field(description="The simulation being varied")
    variant_id: str
    spec: SimSpec = Field(description="The original SimSpec with the changes applied")
    code: SolverCode = Field(description="The original simulation's solver, re-run unchanged")


class VariantOutcome(BaseModel):
    variant_id: str
    ok: bool
    playback_key: str | None = None
    result_key: str | None = None
    error: str = ""
    duration_s: float = 0.0


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
# Ways to make one entity's asset; the router (plan stage) orders them per entity and SceneBuild tries them in turn.
MeshRoute = Literal["library", "cad", "generated", "procedural"]
MeshStage = Literal["plan", "library", "cad", "cad_skin", "generate", "postprocess", "physics_rig", "ml_rig",
                    "animate", "assemble", "dress"]


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
    routes: list[MeshRoute] = Field(
        description="Ways to make this asset, best first; each is tried until one passes its evaluation")
    route_reason: str = Field(description="One sentence: why this order")
    search_query: str = Field(description="Model-library search terms (library route); empty otherwise")
    cad_brief: str = Field(
        description="Parts, key dimensions in metres and joints to model (cad route); empty otherwise")
    gen_prompt: str = Field(description="Visual description of the object (generated and library routes); "
                                        "empty for procedural")
    rig: Literal["none", "physics", "ml", "both"]
    clips: list[ClipPlan] = Field(description="Secondary-motion clips; empty unless the body has moving parts")
    poly_budget: int = Field(description="Max triangles after decimation")

    @property
    def mode(self) -> MeshRoute:
        """The first-choice route."""
        return self.routes[0] if self.routes else "procedural"


class AssetPlanOutput(BaseModel):
    assets: list[AssetPlan]


class MeshCapabilities(BaseModel):
    """What this deployment can actually run, so the router never picks a route nobody serves."""

    gpu: bool = Field(default=False, description="A mesh-gpu worker exists (generated route, ML rigging)")
    cad: bool = Field(default=False, description="The CAD sandbox runner is configured (cad route)")
    library_sources: list[str] = Field(default_factory=list, description="Enabled model-library sources")


class PlanAssetsInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    backend: MeshBackend = "procedural"
    capabilities: MeshCapabilities | None = Field(
        default=None, description="None: the mesh worker reads its own environment")


class PlanAssetsOutput(BaseModel):
    assets: list["AssetPlan"]
    eval: "StageEval"
    capabilities: MeshCapabilities = Field(default_factory=MeshCapabilities, description="What the plan assumed")
    cached: list["MeshArtifact"] = Field(default_factory=list, description="Finished assets found in the cache")


class Attribution(BaseModel):
    """Credit for a reused library model (CC-BY requires it; CC0 gets it anyway)."""

    title: str
    author: str
    license: str
    url: str = Field(description="The model's page")
    source: str = Field(description="Library it came from, e.g. objaverse, polypizza")


class LibraryCandidate(BaseModel):
    source: str
    id: str
    name: str
    license: str = Field(description="Normalised licence; only CC0 and CC-BY variants pass the licence filter")
    author: str
    url: str = Field(description="The model's page, for attribution")
    download_url: str = Field(default="", description="Direct GLB URL when the source's search provides one")
    thumb_url: str = ""
    tris: int = 0
    animated: bool = False

    def attribution(self) -> Attribution:
        return Attribution(title=self.name, author=self.author, license=self.license, url=self.url,
                           source=self.source)


class LibraryShortlist(BaseModel):
    """Vision pick among numbered thumbnails (LLM output)."""

    picks: list[int] = Field(description="Numbers of the thumbnails that show the requested object, best first")
    scores: list[int] = Field(description="0-10 per pick, same order: how well it shows the requested object")
    reason: str = Field(description="One sentence")


class LibrarySearchOutput(BaseModel):
    candidates: list[LibraryCandidate] = Field(description="Shortlisted, best first")
    eval: "StageEval"
    exhausted: bool = Field(default=False, description="No query variant left for this attempt number")


class LibraryFetchInput(BaseModel):
    job_id: str
    plan: "AssetPlan"
    candidate: LibraryCandidate
    attempt: int = 0


class CadWriteInput(BaseModel):
    job_id: str
    plan: "AssetPlan"
    spec: SimSpec
    attempt: int = 0
    critique: str = ""
    previous_code: str = ""


class CadWriteOutput(BaseModel):
    """LLM output: a complete build123d module (see omnific_mesh.cad for the contract)."""

    code: str = Field(description="Complete Python module defining build() and JOINTS")
    parts: list[str] = Field(description="The part labels build() returns")
    notes: str = Field(description="One sentence on the design, or what changed since the last attempt")


class CadRunInput(BaseModel):
    job_id: str
    entity: str
    code: str
    attempt: int = 0
    timeout_s: int = Field(default=180, ge=10, le=900)


class CadRunOutput(BaseModel):
    ok: bool
    code_key: str
    result_key: str | None = Field(default=None, description="result.zip: cad.json, parts.npz, model.step")
    step_key: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0


class CadEvaluateInput(BaseModel):
    job_id: str
    plan: "AssetPlan"
    spec: SimSpec
    attempt: int = 0
    run: CadRunOutput
    parts: list[str] = Field(default_factory=list, description="Labels the writer said build() returns")


class MeshArtifact(BaseModel):
    """One entity's asset at some stage. Procedural assets carry no GLB: the scene builds them from `asset`."""

    entity: str
    source: Literal["procedural", "generated", "library", "cad"]
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
    attribution: Attribution | None = Field(default=None, description="Set for library models")
    step_key: str = Field(default="", description="STEP file of a CAD model (download)")


class CadSkinInput(BaseModel):
    job_id: str
    plan: "AssetPlan"
    cad: MeshArtifact = Field(description="The finished CAD model: keeps its dimensions, joints, clips and STEP file")
    generated: MeshArtifact = Field(description="The post-processed generated mesh that supplies the surface")
    attempt: int = 0


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
    final: Literal["generated_rigged", "generated", "library_rigged", "library", "cad_rigged", "cad", "procedural"]
    attempts: int
    routes_tried: list[MeshRoute] = Field(default_factory=list)
    route_used: MeshRoute = "procedural"
    route_reason: str = ""
    attribution: Attribution | None = None
    step_key: str = Field(default="", description="STEP file of the CAD model (GET /jobs/{id}/assets/{entity}/model.step)")
    evals: list[StageEval]
    notes: list[str] = Field(default_factory=list)


class SceneReport(BaseModel):
    backend: MeshBackend
    entities: list[EntityReport]
    scene_checks: list[CheckResult] = Field(default_factory=list)


# --- Asset picker (the user chooses each object's model while the job builds) ---------------------------

PickRoute = Literal["auto", "library", "cad", "generated", "procedural"]


class AssetOption(BaseModel):
    candidate: LibraryCandidate
    thumb_key: str = Field(default="", description="Stored thumbnail (served by the API from the job's own choices)")
    score: int = Field(default=0, description="Vision shortlist score 0-10")
    reason: str = ""
    status: Literal["shortlisted", "passed", "failed"] = "shortlisted"
    detail: str = Field(default="", description="Why it passed or failed the full evaluation")


class EntityChoices(BaseModel):
    entity: str
    kind: str
    default_route: MeshRoute = Field(description="What Omnific would use without a pick (the router's first route)")
    routes_available: list[MeshRoute]
    query: str = Field(default="", description="The model-library search behind the options")
    options: list[AssetOption] = Field(default_factory=list)
    note: str = ""


class AssetChoices(BaseModel):
    entities: list[EntityChoices] = Field(default_factory=list)
    ready: bool = Field(default=False, description="Every pre-evaluation has finished")


class AssetPick(BaseModel):
    entity: str
    route: PickRoute = "auto"
    candidate_id: str = Field(default="", description="route=library: '<source>/<id>' of the chosen option")
    candidate: LibraryCandidate | None = Field(default=None, description="Filled by the workflow from its choices")


class AssetPicks(BaseModel):
    """Sent as the `pick_assets` signal."""

    picks: list[AssetPick] = Field(default_factory=list)


class AssetChoicesInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    parent_workflow_id: str = Field(description="SimulationJob to signal with choices_update")


class EntityChoicesInput(BaseModel):
    job_id: str
    plan: "AssetPlan"
    routes_available: list[MeshRoute]


class PrefetchedAsset(BaseModel):
    entity: str
    candidate_id: str
    artifact: "MeshArtifact"


class AssetChoicesOutput(BaseModel):
    choices: AssetChoices
    prefetched: list[PrefetchedAsset] = Field(default_factory=list, description="Options that passed, ready to use")


class SceneBuildInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    backend: MeshBackend = "procedural"
    picks: list[AssetPick] = Field(default_factory=list, description="The user's choices (asset picker)")
    prefetched: list[PrefetchedAsset] = Field(default_factory=list)
    length_scale: float | None = Field(
        default=None, description="Scene units per metre from the playback: entities with geometry get true sizes")
    max_attempts: int = Field(default=3, ge=1, le=10)
    gpu_wait_minutes: float = Field(
        default=15.0, gt=0, description="How long a GPU stage may wait for a GPU worker before falling back"
    )


class AssembleInput(BaseModel):
    job_id: str
    spec: SimSpec
    artifacts: list[MeshArtifact]
    length_scale: float | None = Field(default=None, description="Scene units per metre (true sizes); None = MVP")


class AssembleOutput(BaseModel):
    scene_key: str
    entity_assets: dict[str, str]
    eval: StageEval


class RouteOutcome(BaseModel):
    kind: str
    route: MeshRoute
    passed: bool
    attempts: int
    failed_checks: list[str] = Field(default_factory=list)


class RouteStatsInput(BaseModel):
    job_id: str
    outcomes: list[RouteOutcome]


class CheckSceneInput(BaseModel):
    job_id: str
    spec: SimSpec
    scene_key: str


# --- Scene dressing (Blender; docs/architecture.md "Scene dressing") -------------------------------------
# Blender restyles, decorates and lights the scene SceneBuild assembled, and exports the run as a .blend. It never
# moves anything: motion comes only from the evaluated playback.json, and the entities' nodes stay byte-identical.

DressAgent = Literal["look", "scenery", "lighting"]


class DressScript(BaseModel):
    agent: DressAgent
    code: str = Field(description="One complete Blender Python (bpy) script")
    notes: str = Field(description="One sentence: what the script does")


class DressDraft(BaseModel):
    """The dress writer's LLM output (strict structured output: every field required)."""

    scripts: list[DressScript] = Field(description="One script per agent, in the order look, scenery, lighting")
    props: list[str] = Field(description="Ids of the listed props the scenery script places with omnific.prop; "
                                         "empty if none")


class DressScripts(BaseModel):
    """What dress_write returns: the scripts and the props they use."""

    scripts: list[DressScript]
    props: list[str] = Field(default_factory=list)


class DressPropOption(BaseModel):
    """A ready-made CC0 prop the Scenery script may place (from Poly Haven)."""

    id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    size: list[float] = Field(default_factory=list, description="Approximate real size in metres, when known")


class DressProp(BaseModel):
    id: str
    name: str
    key: str = Field(description="Stored GLB, re-encoded by our own writer")
    credit: str


class ScenePropsInput(BaseModel):
    job_id: str
    query: str = Field(default="", description="Catalogue mode: words describing the setting")
    ids: list[str] = Field(default_factory=list, description="Fetch mode: the props to download")


class ScenePropsOutput(BaseModel):
    options: list[DressPropOption] = Field(default_factory=list)
    props: list[DressProp] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DressWriteInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    playback_key: str
    scene_key: str
    scene_report: SceneReport | None = None
    attempt: int = 0
    critique: str = Field(default="", description="Why the previous attempt failed its evaluation")
    previous: list[DressScript] = Field(default_factory=list, description="The previous attempt's scripts")
    prop_options: list[DressPropOption] = Field(default_factory=list, description="Props the scenery may place")


class BlenderRunInput(BaseModel):
    job_id: str
    mode: Literal["dress", "export"]
    scene_key: str
    playback_key: str
    entities: list[str]
    scripts: list[DressScript] = Field(default_factory=list)
    props: list[DressProp] = Field(default_factory=list, description="Prop GLBs the scripts may place")
    prefix: str = Field(description="Output keys go under jobs/<job_id>/<prefix>/")
    stills: int = Field(default=4, ge=0, le=8, description="Evaluation stills (dress mode)")
    timeout_s: int = 900


class BlenderRunOutput(BaseModel):
    ok: bool
    report_key: str | None = None
    scene_key: str | None = Field(default=None, description="The dressed scene.gltf (dress mode)")
    blend_key: str | None = None
    still_keys: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0


class DressEvaluateInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    attempt: int
    run: BlenderRunOutput
    scene_checks: list[CheckResult] = Field(default_factory=list, description="check_scene on the dressed scene")


class DressReport(BaseModel):
    dressed: bool = Field(description="The viewer shows the Blender-dressed scene (else SceneBuild's own)")
    attempts: int
    evals: list[StageEval] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    scripts: list[DressScript] = Field(default_factory=list, description="The scripts that produced the scene")
    props: list[DressProp] = Field(default_factory=list, description="Ready-made props placed in the scenery")


class SceneDressInput(BaseModel):
    job_id: str
    prompt: str
    spec: SimSpec
    playback_key: str
    scene_key: str
    scene_report: SceneReport | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)


class SceneDressOutput(BaseModel):
    scene_key: str = Field(description="The dressed scene, or the input scene when dressing never passed")
    blend_key: str | None = None
    report: DressReport


class BlendExportInput(BaseModel):
    """A .blend for any run (e.g. a Tune variant): no LLM, the harness's export mode."""

    job_id: str
    scene_key: str
    playback_key: str
    entities: list[str]
    prefix: str


# --- Workflow state -------------------------------------------------------------------------

Stage = Literal[
    "interpreting_inputs", "planning_edit",
    "clarifying", "awaiting_clarification", "researching", "modeling", "coding", "running", "evaluating",
    "awaiting_asset_picks", "packaging",
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
    mesh_backend: Literal["procedural", "generated"] = "procedural"
    edit: "EditSummary | None" = Field(default=None, description="Set on a follow-up version: what changed")
    raw_scene_key: str | None = Field(default=None, description="SceneBuild's scene before Blender dressing")
    blend_key: str | None = Field(default=None, description="The run as a .blend (plays on Space in Blender)")
    dress_report: "DressReport | None" = None
    asset_choices: AssetChoices | None = Field(default=None, description="What the user could pick from")
    asset_picks: list[AssetPick] = Field(default_factory=list, description="What they picked")


# --- Follow-up edits (versions) ---------------------------------------------------------------
# A version is a job of its own (own workflow id and artifacts) linked to the job it changes; every job endpoint works
# on it unchanged. Its workflow re-runs only what the change touches.

EditKind = Literal["visual", "parameters", "structural", "unclear"]


class EntityLook(BaseModel):
    entity: str = Field(description="An existing entity name")
    look: str = Field(description="How it should look now, in a few words: 'a red vintage race car'")


class EditPlan(BaseModel):
    """The edit classifier's re-run plan (strict structured output: lists, every field required)."""

    kind: EditKind = Field(
        description="visual: only how things look (colour, model, style) - no physics changes. parameters: only "
        "values that already exist (parameters, initial values, the duration). structural: anything else (new or "
        "removed objects, new physics, a new setting/environment, changed equations). unclear: you cannot tell what "
        "they want without asking"
    )
    summary: str = Field(description="One plain sentence the user will read: what this version changes")
    changes: VariantChanges = Field(description="kind=parameters: the new values, in the units the spec uses")
    stop_fixed: bool = Field(
        description="kind=parameters with changes.end set: true when the user asked for an exact duration"
    )
    looks: list[EntityLook] = Field(description="kind=visual: the entities whose look changes")
    questions: list[ClarifyQuestion] = Field(description="kind=unclear: 1-3 questions; empty otherwise")


class EditClassifyInput(BaseModel):
    job_id: str
    request: str = Field(description="The change in the user's words (plus what attached sketches/voice notes say)")
    spec: SimSpec = Field(description="The version being changed")
    answers: list[Clarification] = Field(
        default_factory=list, description="Answers to the classifier's own questions (second pass)"
    )


class EditContext(BaseModel):
    """What a follow-up version starts from; the API fills it from the version being changed."""

    root_job_id: str = Field(description="Version 1: the job every version descends from")
    base_job_id: str = Field(description="The version being changed")
    base_version: int = Field(default=1, ge=1, description="Its number (an edit may start from an older version)")
    version: int = Field(ge=2)
    request: str
    inputs: list[InputRef] = Field(default_factory=list)
    base: "JobOutcome"
    code: str = Field(description="The base version's solver source, reused when only values change")


class EditSummary(BaseModel):
    root_job_id: str
    base_job_id: str
    base_version: int = 1
    version: int
    request: str = Field(description="The user's words")
    kind: EditKind
    summary: str
    diff: list[str] = Field(default_factory=list, description="What actually changed in the SimSpec, line by line")


SceneOutput.model_rebuild()  # forward references
JobRequest.model_rebuild()
JobOutcome.model_rebuild()
EditContext.model_rebuild()
PlanAssetsOutput.model_rebuild()
EntityChoicesInput.model_rebuild()
PrefetchedAsset.model_rebuild()
