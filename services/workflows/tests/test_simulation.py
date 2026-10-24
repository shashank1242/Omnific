import asyncio
import uuid

import pytest
from omnific_common.contracts import (
    ACTIVITY,
    CheckResult,
    ClarifyOutput,
    ClarifyQuestion,
    EvalReport,
    JobOutcome,
    JobRequest,
    PlaybackOutput,
    ResearchOutput,
    ReviewDecision,
    RunOutput,
    SceneOutput,
    SolverCode,
    TaskQueue,
)
from omnific_sim.spec import Domain, Fidelity, SimSpec, TimeSpan
from omnific_workflows.ingest import DocumentIngest
from omnific_workflows.simulation import SimulationJob
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def make_spec(title="s") -> SimSpec:
    return SimSpec(
        title=title, prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC,
        assumptions=[], parameters=[], state=[], equations=[], time=TimeSpan(end=1.0),
    )


class Stubs:
    """Records calls (as dicts); behaviour is scripted per test."""

    def __init__(self, run_oks=(), eval_reports=None, fine_oks=(), questions=(), clarifier_fails=False):
        self.questions: list[ClarifyQuestion] = list(questions)
        self.clarifier_fails = clarifier_fails
        self.calls: dict[str, list] = {}
        self.run_oks = list(run_oks)  # refine=1 ok per attempt (default True)
        self.fine_oks = list(fine_oks)  # refine=2 ok per attempt (default True)
        self.eval_reports = list(eval_reports or [EvalReport(passed=True, checks=[])])
        self.n_models = 0

    def rec(self, name, arg):
        self.calls.setdefault(name, []).append(arg)

    def activities(self):
        s = self

        @activity.defn(name=ACTIVITY[TaskQueue.CLARIFIER])
        async def clarify(inp: dict) -> ClarifyOutput:
            s.rec("clarify", inp)
            if s.clarifier_fails:
                raise ApplicationError("llm down", non_retryable=True)
            return ClarifyOutput(questions=s.questions)

        @activity.defn(name=ACTIVITY[TaskQueue.RESEARCHER])
        async def research(inp: dict) -> ResearchOutput:
            s.rec("research", inp)
            return ResearchOutput(summary="x", phenomena=[], facts=[])

        @activity.defn(name=ACTIVITY[TaskQueue.MODELER])
        async def build_spec(inp: dict) -> SimSpec:
            s.rec("model", inp)
            s.n_models += 1
            return make_spec(f"spec{s.n_models}")

        @activity.defn(name=ACTIVITY[TaskQueue.SOLVER_CODER])
        async def write_solver(inp: dict) -> SolverCode:
            s.rec("code", inp)
            return SolverCode(code=f"code{len(s.calls['code'])}")

        @activity.defn(name=ACTIVITY[TaskQueue.SANDBOX])
        async def run_solver(inp: dict) -> RunOutput:
            s.rec("run", inp)
            oks = s.run_oks if inp["refine"] == 1 else s.fine_oks
            i = inp["attempt"]
            ok = oks[i] if i < len(oks) else True
            tag = f"a{i}-r{inp['refine']}"
            return RunOutput(ok=ok, result_key=f"res-{tag}" if ok else None, code_key=f"c-{tag}",
                             stderr="" if ok else f"Traceback boom {tag}")

        @activity.defn(name=ACTIVITY[TaskQueue.EVALUATOR])
        async def evaluate(inp: dict) -> EvalReport:
            s.rec("eval", inp)
            if len(s.eval_reports) > 1:
                return s.eval_reports.pop(0)
            return s.eval_reports[0]

        @activity.defn(name=ACTIVITY[TaskQueue.DATA])
        async def prepare_playback(inp: dict) -> PlaybackOutput:
            s.rec("data", inp)
            return PlaybackOutput(playback_key="pb", n_frames=3)

        @activity.defn(name=ACTIVITY[TaskQueue.MESH])
        async def build_scene(inp: dict) -> SceneOutput:
            s.rec("mesh", inp)
            return SceneOutput(scene_key="sc", entity_assets={})

        return {
            TaskQueue.CLARIFIER: clarify, TaskQueue.RESEARCHER: research, TaskQueue.MODELER: build_spec,
            TaskQueue.SOLVER_CODER: write_solver, TaskQueue.SANDBOX: run_solver,
            TaskQueue.EVALUATOR: evaluate, TaskQueue.DATA: prepare_playback,
            TaskQueue.MESH: build_scene,
        }


@pytest.fixture
async def env():
    try:
        e = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001 - e.g. no network to download the test server
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with e:
        yield e


class _Group:
    def __init__(self, workers):
        self.workers = workers

    async def __aenter__(self):
        for w in self.workers:
            await w.__aenter__()

    async def __aexit__(self, *exc):
        for w in reversed(self.workers):
            await w.__aexit__(*exc)


def make_workers(env, acts):
    return _Group(
        [Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SimulationJob, DocumentIngest])]
        + [Worker(env.client, task_queue=q.value, activities=[a]) for q, a in acts.items()]
    )


async def start(env, job_id, max_revisions=3, **kw):
    return await env.client.start_workflow(
        "SimulationJob", JobRequest(job_id=job_id, prompt="p", max_revisions=max_revisions, **kw),
        id=job_id, task_queue=TaskQueue.WORKFLOW.value, result_type=JobOutcome,
    )


async def wait_stage(handle, stage, message=None):
    st = None
    for _ in range(400):
        st = await handle.query("status")
        if st and st["stage"] == stage and (message is None or st["message"] == message):
            return st
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached {stage}: {st}")


async def review(handle, round_n, approved, notes="", blame=None):
    await wait_stage(handle, "awaiting_review", f"review round {round_n}")
    await handle.signal(
        "review", ReviewDecision(approved=approved, reviewer="alice", notes=notes, blame=blame))


async def run_job(env, stubs: Stubs, decisions=((True, "", None),), max_revisions=3, **kw):
    """Run to completion, answering each review round with `decisions` (approved, notes, blame)."""
    job_id = uuid.uuid4().hex
    async with make_workers(env, stubs.activities()):
        handle = await start(env, job_id, max_revisions, **kw)
        for n, (approved, notes, blame) in enumerate(decisions, 1):
            await review(handle, n, approved, notes, blame)
        out = await handle.result()
        status = await handle.query("status")
    return out, status


async def test_happy_path_approve(env):
    stubs = Stubs()
    out, status = await run_job(env, stubs)
    assert out.status == "approved"
    assert out.revisions == 1
    assert out.playback_key == "pb" and out.scene_key == "sc"
    assert out.result_key == "res-a0-r1"
    assert out.code_key == "c-a0-r1"
    assert len(out.reviews) == 1 and out.reviews[0].approved and out.reviews[0].reviewer == "alice"
    assert len(stubs.calls["data"]) == 1 and len(stubs.calls["mesh"]) == 1
    assert status["stage"] == "approved"
    assert sorted(c["refine"] for c in stubs.calls["run"]) == [1, 2]
    ev = stubs.calls["eval"][0]
    assert ev["result_key"] == "res-a0-r1" and ev["fine_result_key"] == "res-a0-r2"


async def test_draft_query_while_awaiting_review(env):
    stubs = Stubs()
    async with make_workers(env, stubs.activities()):
        handle = await start(env, uuid.uuid4().hex)
        await wait_stage(handle, "awaiting_review")
        draft = await handle.query("draft")
        assert draft["status"] == "awaiting_review"
        assert draft["playback_key"] == "pb" and draft["scene_key"] == "sc"
        await review(handle, 1, True)
        out = await handle.result()
    assert out.status == "approved"


async def test_reject_blame_modeler_rebuilds_spec(env):
    stubs = Stubs()
    out, _ = await run_job(
        env, stubs, decisions=[(False, "wrong gravity", "modeler"), (True, "", None)])
    assert out.status == "approved"
    assert len(stubs.calls["model"]) == 2
    fb = stubs.calls["model"][1]["feedback"]
    assert len(fb) == 1 and "wrong gravity" in fb[0] and "alice" in fb[0]
    assert stubs.calls["code"][1]["previous_code"] is None
    assert out.spec.title == "spec2"
    assert out.review_round == 1
    assert [r.approved for r in out.reviews] == [False, True]


async def test_reject_default_blame_is_modeler(env):
    stubs = Stubs()
    await run_job(env, stubs, decisions=[(False, "meh", None), (True, "", None)])
    assert len(stubs.calls["model"]) == 2


async def test_reject_blame_solver_coder_keeps_spec(env):
    stubs = Stubs()
    out, _ = await run_job(
        env, stubs, decisions=[(False, "energy drifts", "solver_coder"), (True, "", None)])
    assert out.status == "approved"
    assert len(stubs.calls["model"]) == 1
    second = stubs.calls["code"][1]
    assert "energy drifts" in second["feedback"][0]
    assert second["previous_code"] == "code1"
    assert out.spec.title == "spec1"


async def test_rejected_every_round_fails(env):
    stubs = Stubs()
    out, status = await run_job(
        env, stubs, decisions=[(False, "no", "solver_coder"), (False, "still no", None)],
        max_review_rounds=2)
    assert out.status == "failed"
    assert out.failure_reason == "rejected in every review round"
    assert len(out.reviews) == 2
    assert status["stage"] == "failed"


async def test_review_timeout_fails(env):
    stubs = Stubs()
    async with make_workers(env, stubs.activities()):
        handle = await start(env, uuid.uuid4().hex, review_timeout_hours=1)
        out = await handle.result()  # time skipping fires the review timeout
        status = await handle.query("status")
    assert out.status == "failed" and out.failure_reason == "review timed out"
    assert out.playback_key == "pb"
    assert status["stage"] == "failed"


async def test_fine_run_failure_feeds_back_refine(env):
    stubs = Stubs(fine_oks=[False, True])
    out, _ = await run_job(env, stubs)
    assert out.status == "approved"
    assert out.revisions == 2
    assert "refine" in stubs.calls["code"][1]["feedback"][0]
    assert "Traceback boom a0-r2" in stubs.calls["code"][1]["feedback"][0]
    assert len(stubs.calls["eval"]) == 1  # evaluator skipped for the failed revision


async def test_signal_outside_awaiting_review_ignored(env):
    stubs = Stubs()
    acts = stubs.activities()
    gate = asyncio.Event()
    orig = acts[TaskQueue.SANDBOX]

    @activity.defn(name=ACTIVITY[TaskQueue.SANDBOX])
    async def slow_run(inp: dict) -> RunOutput:
        await gate.wait()
        return await orig(inp)

    acts[TaskQueue.SANDBOX] = slow_run
    async with make_workers(env, acts):
        handle = await start(env, uuid.uuid4().hex)
        await wait_stage(handle, "running")
        await handle.signal("review", ReviewDecision(approved=True, reviewer="early"))
        gate.set()
        await wait_stage(handle, "awaiting_review")
        await asyncio.sleep(0.3)
        assert (await handle.query("status"))["stage"] == "awaiting_review"  # early approval dropped
        await review(handle, 1, True)
        out = await handle.result()
    assert [r.reviewer for r in out.reviews] == ["alice"]


async def test_attempts_unique_across_rounds(env):
    bad = EvalReport(passed=False, checks=[
        CheckResult(name="drift", passed=False, detail="x", blame="solver_coder")])
    stubs = Stubs(eval_reports=[bad, EvalReport(passed=True, checks=[]),
                                EvalReport(passed=True, checks=[])])
    out, _ = await run_job(
        env, stubs, decisions=[(False, "redo", "solver_coder"), (True, "", None)])
    assert out.status == "approved"
    runs = stubs.calls["run"]
    assert len(runs) == 6  # 2 revisions in round 0 + 1 in round 1, x 2 refines
    for refine in (1, 2):
        attempts = [r["attempt"] for r in runs if r["refine"] == refine]
        assert sorted(attempts) == [0, 1, 2]


async def test_sandbox_failure_then_success(env):
    stubs = Stubs(run_oks=[False, True])
    out, _ = await run_job(env, stubs)
    assert out.status == "approved"
    assert out.revisions == 2
    first, second = stubs.calls["code"]
    assert first["feedback"] == [] and first["previous_code"] is None
    assert "Traceback boom a0-r1" in second["feedback"][0]
    assert second["previous_code"] == "code1"
    assert len(stubs.calls["model"]) == 1


async def test_modeler_blame_rebuilds_spec(env):
    bad = EvalReport(passed=False, checks=[
        CheckResult(name="dims", passed=False, detail="units wrong", blame="modeler")])
    stubs = Stubs(eval_reports=[bad, EvalReport(passed=True, checks=[])])
    out, _ = await run_job(env, stubs)
    assert out.status == "approved"
    assert len(stubs.calls["model"]) == 2
    assert stubs.calls["model"][1]["feedback"] == ["dims: units wrong"]
    assert out.spec.title == "spec2"
    assert stubs.calls["code"][1]["previous_code"] is None


async def test_exhausted_revisions_fails(env):
    bad = EvalReport(passed=False, checks=[
        CheckResult(name="drift", passed=False, detail="too much", blame="solver_coder")])
    stubs = Stubs(eval_reports=[bad])
    out, status = await run_job(env, stubs, decisions=[], max_revisions=2)
    assert out.status == "failed"
    assert out.revisions == 3
    assert out.failure_reason == "evaluation failed"
    assert len(stubs.calls["code"]) == 3
    assert stubs.calls["code"][1]["feedback"] == ["drift: too much"]
    assert "data" not in stubs.calls
    assert status["stage"] == "failed"
