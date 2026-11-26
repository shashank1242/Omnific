"""The simulation guide: its record of the run, and answers grounded in it."""

import json

from omnific_common.contracts import (
    ChatInput,
    ChatMessage,
    ChatReply,
    CheckResult,
    EvalReport,
    Fact,
    ResearchOutput,
)
from omnific_common.storage import LocalStore
from omnific_researcher import chat
from omnific_sim.spec import (
    Citation,
    Domain,
    Entity,
    Equation,
    Fidelity,
    Parameter,
    SimSpec,
    StateVariable,
    TimeSpan,
)
from openai.lib._pydantic import to_strict_json_schema

SPEC = SimSpec(
    title="Oobleck splash", prompt="rifles shoot a barrel of oobleck", domains=[Domain.MECHANICS],
    fidelity=Fidelity.HEURISTIC, assumptions=["the barrel is rigid"],
    parameters=[Parameter(name="rho", value=1600.0, unit="kg/m**3", description="oobleck density",
                          source=Citation(title="Handbook", url="https://example.org"))],
    state=[StateVariable(name="crest", unit="m", initial_value=0.0, description="crown height")],
    equations=[Equation(label="crest", expression="d(crest)/dt = v", description="rise")],
    time=TimeSpan(end=2.0), entities=[Entity(name="barrel", kind="barrel", look="blue plastic drum")],
    departures=["The shots themselves are not simulated: a matched disturbance stands in."],
)
PLAYBACK = {
    "t": [0.0, 1.0, 2.0], "unit_t": "s", "stop": {"rule": "fixed"},
    "series": {"crest": {"unit": "m", "values": [0.0, 0.2, 0.1]}},
    "outputs": [{"label": "Peak crown height", "state": "crest", "measure": "max", "value": 0.2, "unit": "m", "at": 1.0,
                 "why": "from the liquid level"}],
    "phases": [{"name": "Impact", "start": 0.0, "end": 1.0, "description": "the crown rises"}],
    "entities": [{"name": "barrel", "enlarged": 1}], "effects": [{"name": "splash", "enlarged": 3.0}],
}


def inp(**kw):
    base = {"job_id": "j", "prompt": "rifles shoot a barrel of oobleck", "spec": SPEC, "playback_key": "pb.json",
            "messages": [ChatMessage(role="user", content="How high does it splash?")], "view_time": 0.5,
            "research": ResearchOutput(summary="shear thickening", phenomena=["jamming"],
                                       facts=[Fact(name="rho", value=1600, unit="kg/m**3", description="",
                                                   citation=Citation(title="Handbook", url="u"))]),
            "eval_report": EvalReport(passed=True, checks=[CheckResult(name="units", passed=True, detail="")],
                                      critic_summary="plausible")}
    return ChatInput(**{**base, **kw})


def test_the_record_holds_what_was_and_was_not_simulated():
    text = chat.record(inp(), PLAYBACK)
    for needle in ("rifles shoot a barrel of oobleck", "NOT simulated as asked", "matched disturbance",
                   "rho = 1600 kg/m**3 (oobleck density) [source: Handbook]", "crest [m], starts at 0: crown height",
                   "crest: d(crest)/dt = v", "barrel (barrel, blue plastic drum)", "shear thickening",
                   "Checks: all passed", "plausible", "Peak crown height: 0.2 m at t = 1 s",
                   "Stage 'Impact'", "splash x3", "The viewer is at t = 0.5 s; state values there: crest = 0.1 m",
                   "start 0, end 0.1, min 0, max 0.2"):
        assert needle in text, needle
    assert "x1" not in text  # true-size things are not listed as enlarged


def test_interpolation_stays_inside_the_run():
    assert chat._at([0, 1], [0, 10], 0.25) == 2.5
    assert chat._at([0, 1], [0, 10], 2.0) is None
    assert chat._at([0, 1], [None, 10], 0.5) is None
    assert chat._at([0, 1], [10, None], 0.5) is None
    assert chat._at([0, 1], [10, None], 0.0) == 10


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def structured(self, role, instructions, text, schema):
        self.calls.append((role, instructions, text))
        return schema(answer="About 0.2 m.", grounded_in=["simulation"], follow_ups=["a", " ", "b", "c", "d"])


async def test_answers_from_the_record_and_the_conversation(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    store.put("pb.json", json.dumps(PLAYBACK).encode())
    llm = FakeLLM()
    monkeypatch.setattr(chat, "llm", llm)
    monkeypatch.setattr(chat, "store_from_env", lambda: store)
    history = [ChatMessage(role="user", content="hi"), ChatMessage(role="assistant", content="Hello."),
               ChatMessage(role="user", content="How high does it splash?")]
    out = await chat.answer_question(inp(messages=history))
    assert out.answer == "About 0.2 m." and out.follow_ups == ["a", "b", "c"]
    role, instructions, text = llm.calls[0]
    assert role.value == "MODEL_GENERAL" and "never how to build" in instructions
    assert "Peak crown height" in text and text.rstrip().endswith("User: How high does it splash?")
    assert "You: Hello." in text


async def test_a_missing_playback_still_gets_an_answer_and_no_question_gets_a_prompt(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(chat, "llm", llm)

    def broken():
        raise RuntimeError("store down")

    monkeypatch.setattr(chat, "store_from_env", broken)
    assert (await chat.answer_question(inp())).answer == "About 0.2 m."
    assert "Solved results" not in llm.calls[0][2]
    out = await chat.answer_question(inp(messages=[ChatMessage(role="assistant", content="hi")]))
    assert "Ask me" in out.answer and len(llm.calls) == 1


def test_contracts_are_strict():
    for model in (ChatReply, ChatInput):
        to_strict_json_schema(model)
