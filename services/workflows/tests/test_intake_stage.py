import uuid

from omnific_common.contracts import InputRef, IntakeItem, IntakeOutput
from test_simulation import Stubs, env, make_workers, run_job, start  # noqa: F401

SKETCH = InputRef(input_id="a" * 32, key=f"inputs/{'a' * 32}/ramp.png", kind="image", mime="image/png",
                  filename="ramp.png")
ITEM = IntakeItem(input_id="a" * 32, kind="image", filename="ramp.png", text="2 kg block on a 30° ramp")
EFFECTIVE = "slide it\n\nFrom the sketch/photo `ramp.png`:\n2 kg block on a 30° ramp"


async def test_no_inputs_skips_intake(env):  # noqa: F811
    stubs = Stubs()
    out, _ = await run_job(env, stubs, clarify=False)
    assert "intake" not in stubs.calls
    assert out.effective_prompt == "" and out.intake == []
    assert stubs.calls["research"][0]["prompt"] == "p"


async def test_effective_prompt_reaches_every_agent(env):  # noqa: F811
    stubs = Stubs(intake=IntakeOutput(effective_prompt=EFFECTIVE, items=[ITEM]))
    out, _ = await run_job(env, stubs, inputs=[SKETCH], prompt="slide it")
    assert stubs.calls["intake"][0]["inputs"][0]["key"] == SKETCH.key
    assert stubs.calls["intake"][0]["prompt"] == "slide it"
    for stage in ("clarify", "research", "model", "eval"):
        assert stubs.calls[stage][0]["prompt"] == EFFECTIVE, stage
    assert out.status == "approved"
    assert out.effective_prompt == EFFECTIVE and out.intake == [ITEM]


async def test_intake_failure_falls_back_to_typed_prompt(env):  # noqa: F811
    stubs = Stubs(intake_fails=True)
    out, _ = await run_job(env, stubs, inputs=[SKETCH], prompt="slide it", clarify=False)
    assert out.status == "approved"
    assert stubs.calls["research"][0]["prompt"] == "slide it"
    assert out.effective_prompt == ""


async def test_nothing_usable_fails_the_job(env):  # noqa: F811
    stubs = Stubs(intake=IntakeOutput(effective_prompt="", items=[], warnings=["ramp.png produced no text"]))
    async with make_workers(env, stubs.activities()):
        handle = await start(env, uuid.uuid4().hex, inputs=[SKETCH], prompt="")
        out = await handle.result()
    assert out.status == "failed" and "attachments" in out.failure_reason
    assert "research" not in stubs.calls
