import pytest
from omnific_common.contracts import (
    Clarification,
    ClarifyQuestion,
    EditClassifyInput,
    EditPlan,
    EntityLook,
    ValueChange,
    VariantChanges,
)
from omnific_modeler import activities, edit
from omnific_sim.spec import SimSpec
from openai.lib._pydantic import to_strict_json_schema
from test_modeler import GOOD, FakeLLM

SPEC = SimSpec.model_validate({
    **GOOD,
    "entities": [{"name": "body", "kind": "sphere", "look": "a brass ball"}],
})


def plan(kind="parameters", **kw):
    base = {"kind": kind, "summary": "s", "changes": VariantChanges(), "stop_fixed": False, "looks": [], "questions": []}
    return EditPlan(**{**base, **kw})


def q(i="a"):
    return ClarifyQuestion(id=i, question="?", why="w", kind="text", options=[], unit="", default="x")


def test_good_parameters_kept():
    p = plan(changes=VariantChanges(parameters=[ValueChange(name="k", value=0.2)], end=90.0), stop_fixed=True)
    out = edit.sanitize_plan(SPEC, p, answered=False)
    assert out.kind == "parameters" and out.stop_fixed and out.changes.end == 90.0


def test_unknown_parameter_structural():
    p = plan(changes=VariantChanges(parameters=[ValueChange(name="zzz", value=1)]))
    out = edit.sanitize_plan(SPEC, p, False)
    assert out.kind == "structural" and out.summary == "s" and not out.changes.parameters


def test_empty_parameters_structural():
    assert edit.sanitize_plan(SPEC, plan(), False).kind == "structural"


def test_visual_unknown_entity_structural():
    p = plan("visual", looks=[EntityLook(entity="nope", look="red")])
    assert edit.sanitize_plan(SPEC, p, False).kind == "structural"


def test_visual_good_and_empty():
    p = plan("visual", looks=[EntityLook(entity="body", look="red")])
    assert edit.sanitize_plan(SPEC, p, False).kind == "visual"
    assert edit.sanitize_plan(SPEC, plan("visual"), False).kind == "structural"


def test_unclear_cases():
    assert edit.sanitize_plan(SPEC, plan("unclear"), False).kind == "structural"
    assert edit.sanitize_plan(SPEC, plan("unclear", questions=[q()]), True).kind == "structural"
    out = edit.sanitize_plan(SPEC, plan("unclear", questions=[q("a")] * 5), False)
    assert out.kind == "unclear" and len(out.questions) == 3
    assert len({x.id for x in out.questions}) == 3


def test_questions_cleared_for_parameters():
    p = plan(changes=VariantChanges(parameters=[ValueChange(name="k", value=0.2)]), questions=[q()])
    assert edit.sanitize_plan(SPEC, p, False).questions == []


def test_stop_fixed_without_end():
    p = plan(changes=VariantChanges(parameters=[ValueChange(name="k", value=0.2)]), stop_fixed=True)
    assert edit.sanitize_plan(SPEC, p, False).stop_fixed is False


def test_structural_cleared():
    p = plan("structural", changes=VariantChanges(end=5.0), stop_fixed=True,
             looks=[EntityLook(entity="body", look="x")])
    out = edit.sanitize_plan(SPEC, p, False)
    assert out.changes.end is None and not out.looks and not out.stop_fixed


def test_render_edit_input():
    inp = EditClassifyInput(job_id="j", request="make it red", spec=SPEC, answers=[
        Clarification(question="How much?", answer="2", unit="kg", used_default=False)])
    text = edit.render_edit_input(inp)
    for frag in ("make it red", "k = 0.1 1/second", "T = 350 kelvin", "a brass ball", "end 60 second",
                 "Lab bench", "How much? -> 2 kg"):
        assert frag in text


def test_strict_schema():
    assert to_strict_json_schema(EditPlan)


@pytest.mark.asyncio
async def test_activity(monkeypatch):
    fake = FakeLLM(plan(changes=VariantChanges(parameters=[ValueChange(name="nope", value=1)])),
                   plan(changes=VariantChanges(parameters=[ValueChange(name="k", value=0.3)])))
    monkeypatch.setattr(activities, "llm", fake)
    out = await edit.classify_edit(EditClassifyInput(job_id="j", request="r", spec=SPEC))
    assert out.kind == "structural"
    out = await edit.classify_edit(EditClassifyInput(job_id="j", request="r", spec=SPEC))
    assert out.kind == "parameters" and out.changes.parameters[0].value == 0.3
    assert "Change request" in fake.calls[0][1]
