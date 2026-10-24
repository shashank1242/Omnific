import logging
import os
import re

from omnific_common.contracts import ACTIVITY, Fact, ResearchInput, ResearchOutput, TaskQueue
from omnific_common.llm import LLM, Role, web_search_tool
from omnific_rag.tools import document_tools
from omnific_sim.units import ureg
from temporalio import activity

from .mcp_tools import mcp_tools
from .prompts import RESEARCH_INSTRUCTIONS

log = logging.getLogger(__name__)

llm = LLM()

DOC_URL = re.compile(r"^omnific://doc/[^/#\s]+#page=\d+$")


def valid_fact(fact: Fact) -> bool:
    try:
        ureg.parse_units(fact.unit)
    except Exception as e:  # noqa: BLE001
        log.warning("dropping fact %r: unparsable unit %r (%s)", fact.name, fact.unit, e)
        return False
    return True


def check_citation(fact: Fact, document_ids: list[str]) -> tuple[Fact, str | None]:
    """Enforce the omnific://doc/<id>#page=<n> format for document citations.

    Returns the (possibly fixed) fact and an open-question string if the citation had to be flagged."""
    url = fact.citation.url
    if not url.startswith("omnific://"):
        return fact, None
    if DOC_URL.match(url) and url.split("/")[3].split("#")[0] in document_ids:
        return fact, None
    base = re.match(r"^omnific://doc/([^/#\s?]+)", url)
    if base and base.group(1) in document_ids:
        # right document, but page missing/garbled: keep it, flag it
        fixed = fact.citation.model_copy(update={"url": f"omnific://doc/{base.group(1)}"})
        return (fact.model_copy(update={"citation": fixed}),
                f"Fact '{fact.name}': citation lacks a page number (#page=<n>); verify in the document.")
    log.warning("dropping fact %r: citation %r is not a known document", fact.name, url)
    return fact, f"Fact '{fact.name}' dropped: it cited an unknown document ({url})."


def max_steps() -> int:
    try:
        return max(1, int(os.environ.get("RESEARCH_MAX_STEPS", "8")))
    except ValueError:
        return 8


def render_input(inp: ResearchInput) -> str:
    parts = [f"Prompt:\n{inp.prompt}"]
    if inp.clarifications:
        lines = [
            f"- Q: {c.question}\n  A: {c.answer}{(' ' + c.unit) if c.unit else ''}"
            f"{' (default; user did not specify)' if c.used_default else ' (stated by the user)'}"
            for c in inp.clarifications
        ]
        parts.append("Clarifications (binding context):\n" + "\n".join(lines))
    if inp.document_ids:
        parts.append("The user attached documents; search them with search_documents. Document ids: "
                     + ", ".join(inp.document_ids))
    return "\n\n".join(parts)


@activity.defn(name=ACTIVITY[TaskQueue.RESEARCHER])
async def research(inp: ResearchInput) -> ResearchOutput:
    async with mcp_tools() as mcp:
        tools = document_tools(inp.document_ids) + mcp
        out = await llm.run_tools(
            Role.GENERAL, RESEARCH_INSTRUCTIONS, render_input(inp), ResearchOutput,
            tools=tools, hosted_tools=[web_search_tool()], max_steps=max_steps(),
        )
    kept: list[Fact] = []
    questions = list(out.open_questions)
    for f in out.facts:
        if not valid_fact(f):
            continue
        f, note = check_citation(f, inp.document_ids)
        if note:
            questions.append(note)
            if "dropped" in note:
                continue
        kept.append(f)
    return out.model_copy(update={"facts": kept, "open_questions": questions})
