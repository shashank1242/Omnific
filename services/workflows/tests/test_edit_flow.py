"""Follow-up versions: only what the change touches re-runs, and the outcome says what changed."""

import uuid

import pytest
from omnific_common.contracts import (
    EDIT_ACTIVITY,
    ClarifyAnswer,
    ClarifyAnswers,
    ClarifyQuestion,
    EditContext,
    EditPlan,
    EntityLook,
    JobOutcome,
    ResearchOutput,
    TaskQueue,
    ValueChange,
    VariantChanges,
)
from omnific_sim.spec import Domain, Entity, Fidelity, Parameter, SimSpec, StateVariable, TimeSpan
from omnific_workflows.simulation import embedded_values
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from test_simulation import Stubs, make_workers, review, start, wait_stage


@pytest.fixture
async def env():
    try:
        e = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # noqa: BLE001 - e.g. no network to download the test server
        pytest.skip(f"cannot start Temporal test server: {exc}")
    async with e:
        yield e

BASE = SimSpec(
    title="Drop", prompt="drop a ball", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
    parameters=[Parameter(name="g", value=9.81, unit="m/s**2")],
    state=[StateVariable(name="y", unit="m", initial_value=10.0)], equations=[], time=TimeSpan(end=5.0),
    entities=[Entity(name="ball", kind="sphere")],
)


def plan(kind, summary="the change", **kw) -> EditPlan:
    return EditPlan(kind=kind, summary=summary, changes=kw.pop("changes", VariantChanges()),
                    stop_fixed=kw.pop("stop_fixed", False), looks=kw.pop("looks", []),
                    questions=kw.pop("questions", []))


def base_outcome() -> JobOutcome:
    return JobOutcome(job_id="b" * 32, status="approved", revisions=1, spec=BASE,
                      research=ResearchOutput(summary="r", phenomena=[], facts=[]),
                      code_key="jobs/b/solver.py", result_key="jobs/b/result.npz", playback_key="pb", scene_key="sc",
                      mesh_backend="procedural")


class EditStubs(Stubs):
    def __init__(self, plans=(), classifier_fails=False, model_spec=None, **kw):
        super().__init__(**kw)
        self.plans = list(plans)
        self.classifier_fails = classifier_fails
        self.model_spec = model_spec

    def activities(self):
        acts = super().activities()
        s = self

        @activity.defn(name=EDIT_ACTIVITY)
        async def classify_edit(inp: dict) -> EditPlan:
            s.rec("classify", inp)
            if s.classifier_fails:
                raise ApplicationError("llm down", non_retryable=True)
            return s.plans.pop(0) if len(s.plans) > 1 else s.plans[0]

        if self.model_spec is not None:
            @activity.defn(name="build_spec")
            async def build_spec(inp: dict) -> SimSpec:
                s.rec("model", inp)
                return s.model_spec(inp)

            acts[TaskQueue.MODELER] = [build_spec, classify_edit]
        else:
            acts[TaskQueue.MODELER] = [acts[TaskQueue.MODELER], classify_edit]
        return acts


async def run_edit(env, stubs, request="make it so", answer=None, code="def simulate(spec, refine=1): ..."):
    job_id = uuid.uuid4().hex
    ctx = EditContext(root_job_id="b" * 32, base_job_id="b" * 32, version=2, request=request, base=base_outcome(),
                      code=code)
    async with make_workers(env, stubs.activities()):
        handle = await start(env, job_id, edit=ctx)
        if answer is not None:
            await wait_stage(handle, "awaiting_clarification")
            await handle.signal("clarify", answer)
        await review(handle, 1, True)
        return await handle.result()


async def test_visual_edit_only_rebuilds_the_scene(env):
    stubs = EditStubs(plans=[plan("visual", "The ball looks like a bowling ball.",
                                  looks=[EntityLook(entity="ball", look="a black bowling ball")])])
    out = await run_edit(env, stubs, "make the ball look like a bowling ball")
    assert out.status == "approved" and out.edit.kind == "visual" and out.edit.version == 2
    assert out.result_key == "jobs/b/result.npz" and out.code_key == "jobs/b/solver.py"  # the same run
    assert not any(k in stubs.calls for k in ("model", "code", "run", "eval"))
    assert "ball looks like: a black bowling ball" in out.edit.diff
    assert stubs.calls["mesh"] and stubs.calls["data"]
    assert "Change requested for version 2: make the ball look like a bowling ball" in out.effective_prompt


async def test_parameter_edit_reuses_the_solver(env):
    stubs = EditStubs(plans=[plan("parameters", "Moon gravity.",
                                  changes=VariantChanges(parameters=[ValueChange(name="g", value=1.62)]))])
    out = await run_edit(env, stubs, "use g = 1.62")
    assert out.edit.kind == "parameters"
    assert "model" not in stubs.calls and "code" not in stubs.calls
    runs = stubs.calls["run"]
    assert {r["code"]["code"] for r in runs} == {"def simulate(spec, refine=1): ..."}
    assert runs[0]["spec"]["parameters"][0]["value"] == 1.62
    assert stubs.calls["eval"] and out.edit.diff == ["g: 9.81 m/s**2 → 1.62 m/s**2"]


async def test_exact_duration_makes_a_fixed_run(env):
    stubs = EditStubs(plans=[plan("parameters", "Runs 20 s.", changes=VariantChanges(end=20.0), stop_fixed=True)])
    out = await run_edit(env, stubs, "run it for 20 s")
    assert out.spec.time.end == 20 and out.spec.time.stop == "fixed"


async def test_structural_edit_goes_to_the_modeler_from_the_previous_spec(env):
    def with_ramp(inp):
        prev = SimSpec.model_validate(inp["previous_spec"])
        return prev.model_copy(update={"entities": [*prev.entities, Entity(name="ramp", kind="ramp")]})

    stubs = EditStubs(plans=[plan("structural", "Adds a ramp.")], model_spec=with_ramp)
    out = await run_edit(env, stubs, "add a ramp")
    model = stubs.calls["model"]
    assert len(model) == 1 and model[0]["change_requests"] == ["add a ramp"]
    assert model[0]["previous_spec"]["title"] == "Drop"
    assert out.edit.kind == "structural" and "new object ramp (ramp)" in out.edit.diff


async def test_a_structural_edit_that_changes_nothing_is_retried_once(env):
    stubs = EditStubs(plans=[plan("structural")], model_spec=lambda inp: BASE)
    out = await run_edit(env, stubs, "add a ramp")
    model = stubs.calls["model"]
    assert len(model) == 2 and "came back unchanged" in model[1]["feedback"][0]
    assert out.status == "approved" and out.edit.diff == []


async def test_unclear_edit_asks_then_reclassifies(env):
    q = ClarifyQuestion(id="amount", question="How much heavier?", why="sets the mass", kind="number", options=[],
                        default="2", unit="kg")
    stubs = EditStubs(plans=[plan("unclear", questions=[q]),
                             plan("parameters", "Lower gravity.",
                                  changes=VariantChanges(parameters=[ValueChange(name="g", value=5.0)]))])
    out = await run_edit(env, stubs, "make it fall slower",
                         answer=ClarifyAnswers(answers=[ClarifyAnswer(id="amount", value="5")]))
    first, second = stubs.calls["classify"]
    assert first["answers"] == [] and second["answers"][0]["answer"] == "5"
    assert out.edit.kind == "parameters" and out.questions[0].id == "amount"


async def test_classifier_failure_falls_back_to_the_modeler(env):
    stubs = EditStubs(classifier_fails=True)
    out = await run_edit(env, stubs, "add a ramp")
    assert out.edit.kind == "structural" and stubs.calls["model"][0]["change_requests"] == ["add a ramp"]


async def test_a_plan_naming_an_unknown_value_becomes_structural(env):
    stubs = EditStubs(plans=[plan("parameters", changes=VariantChanges(parameters=[ValueChange(name="mu", value=1)]))])
    out = await run_edit(env, stubs, "add friction")
    assert out.edit.kind == "structural" and stubs.calls["model"]


async def test_a_solver_with_the_old_value_baked_in_is_rewritten(env):
    stubs = EditStubs(plans=[plan("parameters", "Moon gravity.",
                                  changes=VariantChanges(parameters=[ValueChange(name="g", value=1.62)]))])
    out = await run_edit(env, stubs, "use g = 1.62", code="def simulate(spec, refine=1):\n    g = 9.81\n")
    code_calls = stubs.calls["code"]
    assert len(code_calls) == 1 and "g as literal numbers" in code_calls[0]["feedback"][0]
    assert out.edit.kind == "parameters"


def test_embedded_values():
    p = plan("parameters", changes=VariantChanges(parameters=[ValueChange(name="g", value=1.62)],
                                                  initial=[ValueChange(name="y", value=20)]))
    assert embedded_values("g = spec.parameter('g').value", BASE, p) == []
    assert embedded_values("g = 9.81", BASE, p) == ["g"]
    assert embedded_values("y0 = 10.0", BASE, p) == ["y"]
    assert embedded_values("for i in range(100): pass", BASE, p) == []  # 10 inside 100 is not a literal 10
    longer = plan("parameters", changes=VariantChanges(end=20.0))
    assert embedded_values("t = np.linspace(0, 5.0, 200)", BASE, longer) == ["the end time"]
    assert embedded_values("t = np.linspace(spec.time.start, spec.time.end, 200)", BASE, longer) == []
