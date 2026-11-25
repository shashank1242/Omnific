"""SimulationVariant: re-run the original solver with changed values, then rebuild playback (no LLM)."""

import uuid

import pytest
from omnific_common.contracts import (
    ACTIVITY,
    PlaybackInput,
    PlaybackOutput,
    RunInput,
    RunOutput,
    SolverCode,
    TaskQueue,
    VariantInput,
    VariantOutcome,
)
from omnific_common.temporal import workflow_runner
from omnific_sim.spec import Domain, Fidelity, Parameter, SimSpec, TimeSpan
from omnific_workflows.variant import SimulationVariant, variant_key_prefix
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

SPEC = SimSpec(title="Drop", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC, assumptions=[],
               parameters=[Parameter(name="g", value=1.62, unit="m/s**2")], state=[], equations=[],
               time=TimeSpan(end=1.0))


@pytest.fixture
async def env():
    try:
        e = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001 - e.g. no network to download the test server
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with e:
        yield e


async def run_variant(env, run_ok=True, data_fails=False):
    seen: dict = {}

    @activity.defn(name=ACTIVITY[TaskQueue.SANDBOX])
    async def run_solver(inp: RunInput) -> RunOutput:
        seen["run"] = inp
        return RunOutput(ok=run_ok, result_key=f"jobs/{inp.job_id}/attempt-0/result.npz" if run_ok else None,
                         code_key=f"jobs/{inp.job_id}/attempt-0/solver.py", stderr="" if run_ok else "ValueError: g<0",
                         duration_s=0.4)

    @activity.defn(name=ACTIVITY[TaskQueue.DATA])
    async def prepare_playback(inp: PlaybackInput) -> PlaybackOutput:
        seen["data"] = inp
        if data_fails:
            raise RuntimeError("bad result")
        return PlaybackOutput(playback_key=f"jobs/{inp.job_id}/playback.json", n_frames=10)

    job, vid = uuid.uuid4().hex, "abc123def456"
    workers = [
        Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SimulationVariant],
               workflow_runner=workflow_runner()),
        Worker(env.client, task_queue=TaskQueue.SANDBOX.value, activities=[run_solver]),
        Worker(env.client, task_queue=TaskQueue.DATA.value, activities=[prepare_playback]),
    ]
    for w in workers:
        await w.__aenter__()
    try:
        out = await env.client.execute_workflow(
            "SimulationVariant", VariantInput(job_id=job, variant_id=vid, spec=SPEC, code=SolverCode(code="x")),
            id=f"variant-{job}-{vid}", task_queue=TaskQueue.WORKFLOW.value, result_type=VariantOutcome,
        )
    finally:
        for w in reversed(workers):
            await w.__aexit__(None, None, None)
    return out, seen, job, vid


async def test_variant_reruns_the_solver_and_keys_artifacts_under_the_job(env):
    out, seen, job, vid = await run_variant(env)
    assert out.ok and out.playback_key == f"jobs/{job}/variants/{vid}/playback.json"
    assert seen["run"].job_id == variant_key_prefix(job, vid) and seen["run"].spec.parameter("g").value == 1.62
    assert seen["run"].code.code == "x" and seen["run"].refine == 1
    assert seen["data"].result_key == out.result_key


async def test_a_failing_solver_reports_its_error(env):
    out, seen, _, _ = await run_variant(env, run_ok=False)
    assert not out.ok and "g<0" in out.error and "data" not in seen


async def test_a_failing_playback_step_is_reported_not_raised(env):
    out, _, _, _ = await run_variant(env, data_fails=True)
    assert not out.ok and "could not be prepared" in out.error
