import logging

from omnific_common.contracts import ACTIVITY, ClarifyInput, ClarifyOutput, TaskQueue
from omnific_common.llm import LLM, Role
from omnific_rag.tools import document_tools
from temporalio import activity

from .prompts import CLARIFY_INSTRUCTIONS, DOCUMENTS_SUFFIX, REPAIR_SUFFIX
from .validation import keep_valid, validate

log = logging.getLogger(__name__)

llm = LLM()


def _render(inp: ClarifyInput) -> str:
    text = f"Simulation prompt:\n{inp.prompt}"
    if inp.document_titles:
        text += "\n\nAttached documents:\n" + "\n".join(f"- {t}" for t in inp.document_titles)
    return text


@activity.defn(name=ACTIVITY[TaskQueue.CLARIFIER])
async def clarify(inp: ClarifyInput) -> ClarifyOutput:
    """Never fails the job over clarification: invalid questions are repaired once, then dropped."""
    text = _render(inp)
    tools = document_tools(inp.document_ids)
    if tools:  # read the documents first so we don't ask what they already answer
        out = await llm.run_tools(Role.GENERAL, CLARIFY_INSTRUCTIONS + DOCUMENTS_SUFFIX, text, ClarifyOutput,
                                  tools=tools, max_steps=4)
    else:
        out = await llm.structured(Role.GENERAL, CLARIFY_INSTRUCTIONS, text, ClarifyOutput)
    errors = validate(out.questions)
    if errors:
        log.warning("clarify validation failed, attempting repair: %s", errors)
        repair = text + REPAIR_SUFFIX.format(
            errors="\n".join(f"- {e}" for e in errors), previous=out.model_dump_json(indent=1)
        )
        try:
            out = await llm.structured(Role.GENERAL, CLARIFY_INSTRUCTIONS, repair, ClarifyOutput)
        except Exception:
            log.exception("clarify repair call failed")
    return ClarifyOutput(questions=keep_valid(out.questions))
