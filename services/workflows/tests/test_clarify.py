import asyncio
import uuid

import pytest
from omnific_common.contracts import (
    ACTIVITY,
    ClarifyAnswer,
    ClarifyAnswers,
    ClarifyOutput,
    ClarifyQuestion,
    IngestInput,
    IngestOutput,
    TaskQueue,
)
from omnific_workflows.ingest import DocumentIngest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from test_simulation import Stubs, env, make_workers, review, run_job, start, wait_stage  # noqa: F401


def qs():
    return [
        ClarifyQuestion(id="altitude", question="Altitude?", why="air density", kind="number",
                        options=[], unit="meter", default="0"),
        ClarifyQuestion(id="medium", question="Medium?", why="drag", kind="choice",
                        options=["air", "vacuum"], unit="", default="air"),
    ]


async def with_questions(env, stubs, act, **kw):  # noqa: F811
    """Start a job, wait for awaiting_clarification, run `act(handle)`, then approve; return the outcome."""
    async with make_workers(env, stubs.activities()):
        handle = await start(env, uuid.uuid4().hex, **kw)
        await wait_stage(handle, "awaiting_clarification")
        await act(handle)
        await review(handle, 1, True)
        return await handle.result()


def assert_flowed(stubs, out, expected):
    assert [(c.answer, c.used_default) for c in out.clarifications] == expected
    dumped = [c.model_dump() for c in out.clarifications]
    assert stubs.calls["research"][0]["clarifications"] == dumped
    assert stubs.calls["model"][0]["clarifications"] == dumped


async def test_answers_flow_to_research_and_model(env):  # noqa: F811
    stubs = Stubs(questions=qs())

    async def act(h):
        assert [q["id"] for q in await h.query("questions")] == ["altitude", "medium"]
        await h.signal("clarify", ClarifyAnswers(answers=[
            ClarifyAnswer(id="altitude", value="3000"), ClarifyAnswer(id="medium", value="vacuum")]))

    out = await with_questions(env, stubs, act, document_ids=["d1"])
    assert_flowed(stubs, out, [("3000", False), ("vacuum", False)])
    assert out.clarifications[0].unit == "meter"
    assert [q.id for q in out.questions] == ["altitude", "medium"]
    assert stubs.calls["research"][0]["document_ids"] == ["d1"]


async def test_partial_answers_default_rest(env):  # noqa: F811
    stubs = Stubs(questions=qs())

    async def act(h):
        await h.signal("clarify", ClarifyAnswers(answers=[
            ClarifyAnswer(id="medium", value="vacuum"), ClarifyAnswer(id="bogus", value="x")]))

    out = await with_questions(env, stubs, act)
    assert_flowed(stubs, out, [("0", True), ("vacuum", False)])


async def test_use_defaults(env):  # noqa: F811
    stubs = Stubs(questions=qs())

    async def act(h):
        await h.signal("clarify", ClarifyAnswers(
            use_defaults=True, answers=[ClarifyAnswer(id="medium", value="vacuum")]))

    out = await with_questions(env, stubs, act)
    assert_flowed(stubs, out, [("0", True), ("air", True)])


async def test_clarify_timeout_uses_defaults(env):  # noqa: F811
    stubs = Stubs(questions=qs())
    async with make_workers(env, stubs.activities()):
        handle = await start(env, uuid.uuid4().hex, clarify_timeout_hours=1)
        out = await handle.result()  # time skipping fires the clarify, then the review, timeout
    assert out.status == "failed" and out.failure_reason == "review timed out"
    assert_flowed(stubs, out, [("0", True), ("air", True)])


async def test_clarify_false_skips(env):  # noqa: F811
    stubs = Stubs(questions=qs())
    out, _ = await run_job(env, stubs, clarify=False)
    assert "clarify" not in stubs.calls
    assert out.questions == [] and out.clarifications == []
    assert stubs.calls["research"][0]["clarifications"] == []


async def test_no_questions_no_wait(env):  # noqa: F811
    stubs = Stubs()
    out, _ = await run_job(env, stubs)
    assert len(stubs.calls["clarify"]) == 1
    assert out.status == "approved" and out.clarifications == []


async def test_clarifier_failure_proceeds(env):  # noqa: F811
    stubs = Stubs(clarifier_fails=True)
    out, _ = await run_job(env, stubs)
    assert out.status == "approved" and out.clarifications == []


async def test_clarify_signal_early_and_late_ignored(env):  # noqa: F811
    stubs = Stubs(questions=qs())
    acts = stubs.activities()
    gate = asyncio.Event()
    orig = acts[TaskQueue.CLARIFIER]

    @activity.defn(name=ACTIVITY[TaskQueue.CLARIFIER])
    async def slow(inp: dict) -> ClarifyOutput:
        await gate.wait()
        return await orig(inp)

    acts[TaskQueue.CLARIFIER] = slow
    early = ClarifyAnswers(answers=[ClarifyAnswer(id="altitude", value="999")])
    async with make_workers(env, acts):
        handle = await start(env, uuid.uuid4().hex)
        await wait_stage(handle, "clarifying")
        await handle.signal("clarify", early)
        gate.set()
        await wait_stage(handle, "awaiting_clarification")
        await asyncio.sleep(0.3)
        assert (await handle.query("status"))["stage"] == "awaiting_clarification"  # early dropped
        await handle.signal("clarify", ClarifyAnswers(answers=[ClarifyAnswer(id="altitude", value="5")]))
        await review(handle, 1, True)
        await handle.signal("clarify", early)  # late: workflow may already be done
        out = await handle.result()
    assert [c.answer for c in out.clarifications] == ["5", "air"]


# --- DocumentIngest ---------------------------------------------------------------------------

INGEST = IngestInput(document_id="d1", filename="a.pdf", content_type="application/pdf", raw_key="k")


async def run_ingest(env, act):  # noqa: F811
    async with (
        Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[DocumentIngest]),
        Worker(env.client, task_queue=TaskQueue.LIBRARIAN.value, activities=[act]),
    ):
        return await env.client.execute_workflow(
            "DocumentIngest", INGEST, id=uuid.uuid4().hex, task_queue=TaskQueue.WORKFLOW.value,
            result_type=IngestOutput)


async def test_ingest_happy(env):  # noqa: F811
    calls = []

    @activity.defn(name=ACTIVITY[TaskQueue.LIBRARIAN])
    async def ingest(inp: dict) -> IngestOutput:
        calls.append(inp)
        return IngestOutput(document_id=inp["document_id"], title="T", pages=2, n_chunks=5,
                            n_figures=1, n_tables=0)

    out = await run_ingest(env, ingest)
    assert out.title == "T" and out.n_chunks == 5 and calls[0]["raw_key"] == "k"


async def test_ingest_nonretryable_failure(env):  # noqa: F811
    calls = []

    @activity.defn(name=ACTIVITY[TaskQueue.LIBRARIAN])
    async def ingest(inp: dict) -> IngestOutput:
        calls.append(inp)
        raise ApplicationError("unparseable", non_retryable=True)

    with pytest.raises(Exception, match="failed"):
        await run_ingest(env, ingest)
    assert len(calls) == 1


async def test_ingest_retries_transient(env):  # noqa: F811
    calls = []

    @activity.defn(name=ACTIVITY[TaskQueue.LIBRARIAN])
    async def ingest(inp: dict) -> IngestOutput:
        calls.append(inp)
        if len(calls) < 3:
            raise RuntimeError("flaky")
        return IngestOutput(document_id="d1", title="T", pages=1, n_chunks=1, n_figures=0, n_tables=0)

    assert (await run_ingest(env, ingest)).document_id == "d1"
    assert len(calls) == 3
