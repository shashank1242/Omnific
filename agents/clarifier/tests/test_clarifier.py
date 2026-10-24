from omnific_clarifier import activities
from omnific_clarifier.validation import validate
from omnific_common.contracts import ClarifyInput, ClarifyOutput, ClarifyQuestion


def q(id="altitude", kind="number", options=(), unit="meter", default="0"):
    return ClarifyQuestion(id=id, question="?", why="w", kind=kind, options=list(options),
                           unit=unit, default=default)


class FakeLLM:
    def __init__(self, *outs):
        self.outs, self.calls = list(outs), []

    async def structured(self, role, instructions, input, schema, tools=None):
        self.calls.append((role, input))
        out = self.outs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ClarifyOutput(questions=out)


INP = ClarifyInput(job_id="j", prompt="drop a ball", document_titles=["Ball spec"])


async def test_valid_passthrough(monkeypatch):
    good = [q(), q("medium", "choice", ["air", "vacuum"], "", "air")]
    fake = FakeLLM(good)
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.clarify(INP)
    assert out.questions == good and len(fake.calls) == 1
    assert "Ball spec" in fake.calls[0][1]


async def test_empty(monkeypatch):
    monkeypatch.setattr(activities, "llm", FakeLLM([]))
    assert (await activities.clarify(INP)).questions == []


async def test_repair_fixes(monkeypatch):
    fake = FakeLLM([q(unit="florps")], [q(unit="meter")])
    monkeypatch.setattr(activities, "llm", fake)
    out = await activities.clarify(INP)
    assert out.questions[0].unit == "meter" and len(fake.calls) == 2
    assert "pint" in fake.calls[1][1]


async def test_fallback_drops_invalid(monkeypatch):
    bad = [q(unit="florps"), q("Bad Id"), q("c", "choice", ["a"], "", "a"),
           q("c2", "choice", ["a", "b"], "", "z"),
           q("ok", "boolean", unit="", default="true"), q("ok", "boolean", unit="", default="false")]
    monkeypatch.setattr(activities, "llm", FakeLLM(bad, bad))
    out = await activities.clarify(INP)
    assert [x.id for x in out.questions] == ["ok"]


async def test_repair_exception_falls_back(monkeypatch):
    monkeypatch.setattr(activities, "llm", FakeLLM([q(), q(unit="zzz", id="b")], RuntimeError("x")))
    out = await activities.clarify(INP)
    assert [x.id for x in out.questions] == ["altitude"]


def test_validate_caps_and_numeric():
    many = [q(f"q{i}") for i in range(7)]
    assert any("at most" in e for e in validate(many))
    assert validate([q(default="abc")])


async def test_searches_documents_before_asking(monkeypatch):
    from omnific_clarifier import activities
    from omnific_common.contracts import ClarifyInput, ClarifyOutput

    seen = {}

    class FakeLLM:
        async def run_tools(self, role, instructions, input, schema, tools, max_steps=8, **kw):
            seen["tools"] = [t.name for t in tools]
            seen["instructions"] = instructions
            return ClarifyOutput(questions=[])

        async def structured(self, *a, **kw):
            raise AssertionError("with documents attached the clarifier must use the document tools")

    monkeypatch.setattr(activities, "llm", FakeLLM())
    out = await activities.clarify(ClarifyInput(job_id="j", prompt="cool the tile", document_ids=["d1"]))
    assert out.questions == []
    assert "search_documents" in seen["tools"]
    assert "Do NOT ask about anything the documents answer" in seen["instructions"]
