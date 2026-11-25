"""Asset picker: options found while the job builds, the user's picks, and how SceneBuild applies them."""

import uuid
from datetime import timedelta

from omnific_common.contracts import (
    AssembleOutput,
    AssetChoices,
    AssetOption,
    AssetPick,
    AssetPicks,
    AssetPlan,
    CheckResult,
    EntityChoices,
    JobOutcome,
    LibraryCandidate,
    LibrarySearchOutput,
    MeshArtifact,
    MeshCapabilities,
    MeshStageOutput,
    PlanAssetsOutput,
    PrefetchedAsset,
    SceneActivity,
    SceneBuildInput,
    SceneOutput,
    SceneReport,
    StageEval,
    TaskQueue,
)
from omnific_common.temporal import workflow_runner
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan
from omnific_workflows.choices import AssetChoicesWorkflow
from omnific_workflows.dress import BlendExport, SceneDress
from omnific_workflows.ingest import DocumentIngest
from omnific_workflows.scene import SceneBuild
from omnific_workflows.simulation import SimulationJob, resolve_picks
from temporalio import activity, workflow
from temporalio.worker import Worker
from test_simulation import Stubs, _Group, env, review, start, wait_stage  # noqa: F401

SPEC = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], state=[], equations=[], time=TimeSpan(end=1.0),
               entities=[Entity(name="car", kind="car"), Entity(name="ball", kind="sphere")])
CAR = AssetPlan(entity="car", kind="car", routes=["library", "procedural"], route_reason="", search_query="red car",
                cad_brief="", gen_prompt="a red car", rig="none", clips=[], poly_budget=5000)
BALL = AssetPlan(entity="ball", kind="sphere", routes=["procedural"], route_reason="", search_query="", cad_brief="",
                 gen_prompt="", rig="none", clips=[], poly_budget=0)


def cand(i: str) -> LibraryCandidate:
    return LibraryCandidate(source="fake", id=i, name=f"Car {i}", license="CC-BY 4.0", author="ann",
                            url=f"https://x.test/{i}", download_url=f"https://cdn.test/{i}.glb")


def ev(stage, ok, name="x"):
    return StageEval(stage=stage, attempt=0, passed=ok, checks=[CheckResult(name=name, passed=ok, detail="d")])


def lib_art(entity, i) -> MeshArtifact:
    return MeshArtifact(entity=entity, source="library", asset="box", glb_key=f"lib-{i}",
                        attribution=cand(i).attribution())


class Mesh:
    """Fake mesh worker: `bad` candidate ids fail their download; everything else passes."""

    def __init__(self, bad=("1",)):
        self.bad = set(bad)
        self.calls: list[tuple[str, dict]] = []

    def names(self, prefix=""):
        return [n for n, _ in self.calls if n.startswith(prefix)]

    def activities(self):
        s = self

        @activity.defn(name=SceneActivity.PLAN.value)
        async def plan(inp: dict) -> PlanAssetsOutput:
            s.calls.append(("plan", inp))
            return PlanAssetsOutput(assets=[CAR, BALL], eval=ev("plan", True),
                                    capabilities=MeshCapabilities(library_sources=["fake"], cad=True))

        @activity.defn(name=SceneActivity.ASSET_CHOICES.value)
        async def asset_choices(inp: dict) -> EntityChoices:
            s.calls.append(("choices", inp))
            p = inp["plan"]
            return EntityChoices(entity=p["entity"], kind=p["kind"], default_route=p["routes"][0],
                                 routes_available=inp["routes_available"], query=p["search_query"] or p["kind"],
                                 options=[AssetOption(candidate=cand(i), score=9 - int(i)) for i in ("1", "2", "3")])

        @activity.defn(name=SceneActivity.LIBRARY_FETCH.value)
        async def fetch(inp: dict) -> MeshStageOutput:
            cid = inp["candidate"]["id"]
            s.calls.append((f"fetch.{inp['plan']['entity']}.{cid}", inp))
            return MeshStageOutput(artifact=lib_art(inp["plan"]["entity"], cid),
                                   eval=ev("library", cid not in s.bad, "library.fetch"))

        @activity.defn(name=SceneActivity.LIBRARY_SEARCH.value)
        async def search(inp: dict) -> LibrarySearchOutput:
            s.calls.append(("search", inp))
            return LibrarySearchOutput(candidates=[], eval=ev("library", False), exhausted=True)

        @activity.defn(name=SceneActivity.EVALUATE.value)
        async def evaluate(inp: dict) -> StageEval:
            s.calls.append(("evaluate", inp))
            return ev(inp["stage"], True, "critic.library")

        @activity.defn(name=SceneActivity.POSTPROCESS.value)
        async def post(inp: dict) -> MeshStageOutput:
            s.calls.append(("post", inp))
            return MeshStageOutput(artifact=MeshArtifact.model_validate(inp["artifact"]), eval=ev("postprocess", True))

        @activity.defn(name=SceneActivity.PROCEDURAL.value)
        async def procedural(inp: dict) -> MeshStageOutput:
            s.calls.append((f"procedural.{inp['plan']['entity']}", inp))
            return MeshStageOutput(artifact=MeshArtifact(entity=inp["plan"]["entity"], source="procedural",
                                                         asset="box"), eval=ev("generate", True))

        @activity.defn(name=SceneActivity.CACHE.value)
        async def cache(inp: dict) -> MeshArtifact:
            return MeshArtifact.model_validate(inp["artifact"])

        @activity.defn(name=SceneActivity.ASSEMBLE.value)
        async def assemble(inp: dict) -> AssembleOutput:
            s.calls.append(("assemble", inp))
            return AssembleOutput(scene_key="sc", entity_assets={}, eval=ev("assemble", True))

        @activity.defn(name=SceneActivity.ROUTE_STATS.value)
        async def route_stats(inp: dict) -> int:
            return 0

        return [plan, asset_choices, fetch, search, evaluate, post, procedural, cache, assemble, route_stats]


# --- SceneBuild applies picks ------------------------------------------------------------------------------

async def build(env, mesh: Mesh, picks=(), prefetched=()) -> SceneOutput:  # noqa: F811
    async with _Group([Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SceneBuild],
                              workflow_runner=workflow_runner()),
                       Worker(env.client, task_queue=TaskQueue.MESH.value, activities=mesh.activities())]):
        return await env.client.execute_workflow(
            "SceneBuild", SceneBuildInput(job_id="j", prompt="p", spec=SPEC, backend="generated", picks=list(picks),
                                          prefetched=list(prefetched)),
            id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value, result_type=SceneOutput)


def rep(out, entity):
    return next(r for r in out.report.entities if r.entity == entity)


def pick(i):
    return AssetPick(entity="car", route="library", candidate_id=f"fake/{i}", candidate=cand(i))


async def test_pick_uses_the_option_evaluated_during_the_build(env):  # noqa: F811
    m = Mesh()
    out = await build(env, m, [pick("2")], [PrefetchedAsset(entity="car", candidate_id="fake/2",
                                                            artifact=lib_art("car", "2"))])
    car = rep(out, "car")
    assert car.route_used == "library" and car.attribution.url == "https://x.test/2"
    assert m.names("fetch") == [] and m.names("search") == []
    assert any(n.startswith("your choice") and "evaluated while the simulation built" in n for n in car.notes)


async def test_pick_not_yet_evaluated_runs_the_library_stages(env):  # noqa: F811
    m = Mesh()
    out = await build(env, m, [pick("3")])
    assert m.names("fetch") == ["fetch.car.3"] and "evaluate" in m.names() and "post" in m.names()
    assert rep(out, "car").attribution.url == "https://x.test/3"


async def test_failing_pick_falls_back_to_the_planned_routes(env):  # noqa: F811
    m = Mesh(bad=("1",))
    out = await build(env, m, [pick("1")])
    car = rep(out, "car")
    assert car.route_used == "procedural"
    assert any("did not pass" in n for n in car.notes)
    assert m.names("search") == ["search"]  # the router's own library route still had its turn


async def test_type_pick_and_default_with_prefetched(env):  # noqa: F811
    m = Mesh()
    out = await build(env, m, [AssetPick(entity="car", route="procedural")])
    assert rep(out, "car").route_used == "procedural" and m.names("fetch") == [] and m.names("search") == []
    m2 = Mesh()
    out2 = await build(env, m2, [], [PrefetchedAsset(entity="car", candidate_id="fake/2",
                                                     artifact=lib_art("car", "2"))])
    assert rep(out2, "car").attribution.url == "https://x.test/2" and m2.names("search") == []


def test_resolve_picks_keeps_only_what_was_offered():
    choices = AssetChoices(entities=[EntityChoices(
        entity="car", kind="car", default_route="library", routes_available=["library", "cad", "procedural"],
        options=[AssetOption(candidate=cand("1"), status="failed"), AssetOption(candidate=cand("2"))])])
    picks = AssetPicks(picks=[
        AssetPick(entity="car", route="library", candidate_id="fake/2"),
        AssetPick(entity="car", route="library", candidate_id="fake/9"),   # never offered
        AssetPick(entity="car", route="library", candidate_id="fake/1"),   # failed its evaluation
        AssetPick(entity="car", route="generated"),                         # no GPU here
        AssetPick(entity="ghost", route="cad"),                             # no such entity
        AssetPick(entity="car", route="auto"),
    ])
    out = resolve_picks(picks, choices, SPEC)
    assert [(p.route, p.candidate_id) for p in out] == [("library", "fake/2")]
    assert out[0].candidate is not None and out[0].candidate.id == "2"
    cad = resolve_picks(AssetPicks(picks=[AssetPick(entity="car", route="cad", candidate_id="junk")]), choices, SPEC)
    assert [(p.route, p.candidate_id) for p in cad] == [("cad", "")]
    assert resolve_picks(None, choices, SPEC) == [] and resolve_picks(picks, None, SPEC) == []


# --- the whole job -----------------------------------------------------------------------------------------

SEEN: list[SceneBuildInput] = []
RECORD_QUEUE = "test-record"


@activity.defn(name="record_scene_input")
async def record_scene_input(inp: SceneBuildInput) -> None:
    SEEN.append(inp)  # activities run outside the workflow sandbox, so this is the test's own list


@workflow.defn(name="SceneBuild")
class RecordingSceneBuild:
    """Stands in for SceneBuild: records what the job asked for."""

    @workflow.run
    async def run(self, inp: SceneBuildInput) -> SceneOutput:
        await workflow.execute_activity("record_scene_input", inp, task_queue=RECORD_QUEUE,
                                        start_to_close_timeout=timedelta(seconds=30))
        return SceneOutput(scene_key="sc", entity_assets={}, report=SceneReport(backend="generated", entities=[]))


@activity.defn(name="build_spec")
async def build_spec(inp: dict) -> SimSpec:
    return SPEC  # the stub modeler's spec has no entities; the picker needs the car and the ball


def job_workers(env, mesh: Mesh):  # noqa: F811
    acts = Stubs().activities()
    acts[TaskQueue.MESH] = mesh.activities()
    acts[TaskQueue.MODELER] = build_spec
    return _Group(
        [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value,
                workflows=[SimulationJob, DocumentIngest, RecordingSceneBuild, SceneDress, BlendExport,
                           AssetChoicesWorkflow], workflow_runner=workflow_runner())]
        + [Worker(env.client, task_queue=q.value, activities=a if isinstance(a, list) else [a])
           for q, a in acts.items()]
        + [Worker(env.client, task_queue=RECORD_QUEUE, activities=[record_scene_input])])


async def test_job_waits_for_picks_and_passes_them_to_scenebuild(env):  # noqa: F811
    SEEN.clear()
    m = Mesh(bad=("1",))
    async with job_workers(env, m):
        handle = await start(env, uuid.uuid4().hex, mesh_backend="generated")
        st = await wait_stage(handle, "awaiting_asset_picks")
        assert st["message"] == "2 objects to choose"
        choices = AssetChoices.model_validate(await handle.query("asset_choices"))
        car = next(e for e in choices.entities if e.entity == "car")
        assert car.routes_available == ["library", "cad", "procedural"] and len(car.options) == 3
        await handle.signal("pick_assets", AssetPicks(picks=[
            AssetPick(entity="car", route="library", candidate_id="fake/3"),
            AssetPick(entity="ball", route="cad")]))
        await review(handle, 1, approved=True)
        out = JobOutcome.model_validate(await handle.result())
    sent = SEEN[-1]
    assert [(p.entity, p.route, p.candidate_id) for p in sent.picks] == [("car", "library", "fake/3"),
                                                                         ("ball", "cad", "")]
    assert sent.picks[0].candidate is not None and sent.picks[0].candidate.name == "Car 3"
    # the router would use the library for the car: its top 3 were evaluated during the build, "1" failed
    assert sorted(a.candidate_id for a in sent.prefetched) == ["fake/2", "fake/3"]
    statuses = {o.candidate.id: o.status for o in next(e for e in out.asset_choices.entities
                                                        if e.entity == "car").options}
    assert statuses == {"1": "failed", "2": "passed", "3": "passed"}
    assert [p.route for p in out.asset_picks] == ["library", "cad"]
    assert m.names("fetch.ball") == []  # the ball's default is a simple shape: nothing pre-evaluated
    # every pre-evaluated option stores under its own attempt number, clear of SceneBuild's own
    assert [inp["attempt"] for n, inp in m.calls if n.startswith("fetch.car")] == [100, 101, 102]


async def test_job_uses_the_evaluators_choice_when_nobody_picks(env):  # noqa: F811
    SEEN.clear()
    async with job_workers(env, Mesh(bad=())):
        handle = await start(env, uuid.uuid4().hex, mesh_backend="generated", pick_timeout_minutes=5)
        await wait_stage(handle, "awaiting_asset_picks")
        await env.sleep(timedelta(minutes=6))
        await review(handle, 1, approved=True)
        await handle.result()
    assert SEEN[-1].picks == [] and [a.candidate_id for a in SEEN[-1].prefetched] == ["fake/1", "fake/2", "fake/3"]
