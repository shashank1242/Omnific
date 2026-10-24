import logging

from omnific_common.contracts import ACTIVITY, SolverCode, SolverCodeInput, TaskQueue
from omnific_common.llm import LLM, Role
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .prompts import REPAIR_SUFFIX, solver_instructions
from .validation import validate_code

log = logging.getLogger(__name__)

llm = LLM()


def _render_input(inp: SolverCodeInput) -> str:
    parts = [f"SimSpec (JSON):\n{inp.spec.model_dump_json(indent=1)}"]
    if inp.feedback:
        parts.append("Feedback to address:\n" + "\n".join(f"- {f}" for f in inp.feedback))
    if inp.previous_code:
        parts.append(f"Previous code:\n```python\n{inp.previous_code}\n```")
    return "\n\n".join(parts)


@activity.defn(name=ACTIVITY[TaskQueue.SOLVER_CODER])
async def write_solver(inp: SolverCodeInput) -> SolverCode:
    instructions = solver_instructions()
    text = _render_input(inp)
    out = await llm.structured(Role.REASONING, instructions, text, SolverCode)
    errors = validate_code(out.code)
    if errors:
        log.warning("solver code validation failed, attempting repair: %s", errors)
        repair = text + REPAIR_SUFFIX.format(
            errors="\n".join(f"- {e}" for e in errors), previous=out.code
        )
        out = await llm.structured(Role.REASONING, instructions, repair, SolverCode)
        errors = validate_code(out.code)
        if errors:
            raise ApplicationError(
                "solver code failed static validation after self-repair: " + "; ".join(errors),
                non_retryable=True,
            )
    return out
