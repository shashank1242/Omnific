import logging

from omnific_common.contracts import ACTIVITY, Fact, ResearchInput, ResearchOutput, TaskQueue
from omnific_common.llm import LLM, Role, web_search_tool
from omnific_sim.units import ureg
from temporalio import activity

from .prompts import RESEARCH_INSTRUCTIONS

log = logging.getLogger(__name__)

llm = LLM()


def valid_fact(fact: Fact) -> bool:
    try:
        ureg.parse_units(fact.unit)
    except Exception as e:  # noqa: BLE001
        log.warning("dropping fact %r: unparsable unit %r (%s)", fact.name, fact.unit, e)
        return False
    return True


@activity.defn(name=ACTIVITY[TaskQueue.RESEARCHER])
async def research(inp: ResearchInput) -> ResearchOutput:
    out = await llm.structured(
        Role.GENERAL, RESEARCH_INSTRUCTIONS, inp.prompt, ResearchOutput, tools=[web_search_tool()]
    )
    kept = [f for f in out.facts if valid_fact(f)]
    return out.model_copy(update={"facts": kept})
