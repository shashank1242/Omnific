import logging

from omnific_common.contracts import ACTIVITY, ModelInput, TaskQueue
from omnific_common.llm import LLM, Role
from omnific_sim.spec import SimSpec
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .prompts import MODEL_INSTRUCTIONS, REPAIR_SUFFIX
from .validation import validate_spec

log = logging.getLogger(__name__)

llm = LLM()


def _render_input(inp: ModelInput) -> str:
    parts = [
        f"User prompt:\n{inp.prompt}",
        f"Research findings (JSON):\n{inp.research.model_dump_json(indent=1)}",
    ]
    if inp.feedback:
        parts.append("Feedback to address:\n" + "\n".join(f"- {f}" for f in inp.feedback))
    return "\n\n".join(parts)


@activity.defn(name=ACTIVITY[TaskQueue.MODELER])
async def build_spec(inp: ModelInput) -> SimSpec:
    text = _render_input(inp)
    spec = await llm.structured(Role.REASONING, MODEL_INSTRUCTIONS, text, SimSpec)
    errors = validate_spec(spec)
    if errors:
        log.warning("spec validation failed, attempting repair: %s", errors)
        repair = text + REPAIR_SUFFIX.format(
            errors="\n".join(f"- {e}" for e in errors), previous=spec.model_dump_json(indent=1)
        )
        spec = await llm.structured(Role.REASONING, MODEL_INSTRUCTIONS, repair, SimSpec)
        errors = validate_spec(spec)
        if errors:
            raise ApplicationError(
                "SimSpec failed validation after self-repair: " + "; ".join(errors),
                non_retryable=True,
            )
    return spec
