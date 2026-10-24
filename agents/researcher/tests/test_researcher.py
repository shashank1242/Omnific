from omnific_common.contracts import Fact, ResearchInput, ResearchOutput
from omnific_researcher import activities
from omnific_sim.spec import Citation
from openai.lib._pydantic import to_strict_json_schema

CIT = Citation(title="NIST", url="https://webbook.nist.gov")


def fact(name, unit):
    return Fact(name=name, value=1.0, unit=unit, description="d", citation=CIT)


class FakeLLM:
    def __init__(self, out):
        self.out, self.calls = out, []

    async def structured(self, role, instructions, input, schema, tools=None):
        self.calls.append((role, tools))
        return self.out


async def test_drops_bad_units(monkeypatch):
    out = ResearchOutput(
        summary="s", phenomena=["conduction"],
        facts=[fact("cp", "joule/(kilogram*kelvin)"), fact("bad", "furlongs_per_fortnight_squared_x"),
               fact("dimless", "")],
    )
    fake = FakeLLM(out)
    monkeypatch.setattr(activities, "llm", fake)
    res = await activities.research(ResearchInput(job_id="j", prompt="p"))
    assert [f.name for f in res.facts] == ["cp", "dimless"]
    assert res.phenomena == ["conduction"]
    assert fake.calls[0][0].value == "MODEL_GENERAL" and fake.calls[0][1]


def test_strict_schema():
    assert to_strict_json_schema(ResearchOutput)
