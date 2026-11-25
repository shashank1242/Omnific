import logging
import re

from omnific_common.contracts import ACTIVITY, Clarification, ModelInput, TaskQueue
from omnific_common.llm import LLM, Role
from omnific_sim.spec import SimSpec
from omnific_sim.units import ureg
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
    if inp.clarifications:
        lines = []
        for c in inp.clarifications:
            tag = ("DEFAULT used - state as an assumption" if c.used_default
                   else "BINDING user answer, in their own words: interpret it (convert any unit it carries)"
                   if c.custom else "BINDING user answer")
            lines.append(f"- Q: {c.question}\n  A: {c.answer}{(' ' + c.unit) if c.unit else ''} [{tag}]")
        parts.append("Clarifications:\n" + "\n".join(lines))
    if inp.previous_spec is not None and inp.change_requests:
        parts.append(
            "The user already has a version of this simulation and asked for changes. Start from this previous "
            "SimSpec and keep everything they did not ask to change (entities, parameters, values, time span):\n"
            + inp.previous_spec.model_dump_json(indent=1)
        )
        parts.append("Changes the user asked for (BINDING, apply all of them; where one conflicts with an earlier "
                     "clarification answer or document value, the change request wins):\n"
                     + "\n".join(f"- {c}" for c in inp.change_requests))
    if inp.feedback:
        parts.append("Feedback to address:\n" + "\n".join(f"- {f}" for f in inp.feedback))
    return "\n\n".join(parts)


_NUM = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(.*)$")


def _si(value: float, unit: str) -> float | None:
    try:
        q = ureg.Quantity(value, unit or "dimensionless")
        return float(q.to_base_units().magnitude)
    except Exception:  # noqa: BLE001
        return None


def unmatched_clarifications(spec: SimSpec, clarifications: list[Clarification]) -> list[Clarification]:
    """User-given numeric answers (with unit) that no parameter/initial value reproduces (1e-6 relative)."""
    candidates = [(p.value, p.unit) for p in spec.parameters] + [
        (s.initial_value, s.unit) for s in spec.state]
    cands_si = [v for v in (_si(val, u) for val, u in candidates) if v is not None]
    missing = []
    for c in clarifications:
        if c.used_default:
            continue
        m = _NUM.match(c.answer)
        unit = c.unit or (m.group(2).strip() if m else "")
        if not m or not unit:
            continue
        target = _si(float(m.group(1)), unit)
        if target is None:
            continue
        if not any(abs(v - target) <= 1e-6 * max(abs(target), abs(v), 1e-300) for v in cands_si):
            missing.append(c)
    return missing


# Validation also covers the story (effects, phases, what the prompt says happens): allow a few repair rounds.
MAX_REPAIRS = 3


@activity.defn(name=ACTIVITY[TaskQueue.MODELER])
async def build_spec(inp: ModelInput) -> SimSpec:
    text = _render_input(inp)
    spec = await llm.structured(Role.REASONING, MODEL_INSTRUCTIONS, text, SimSpec)
    errors = validate_spec(spec)
    for attempt in range(1, MAX_REPAIRS + 1):  # each round sees the latest spec and only its remaining errors
        if not errors:
            break
        log.warning("spec validation failed, repair %d of %d: %s", attempt, MAX_REPAIRS, errors)
        repair = text + REPAIR_SUFFIX.format(
            errors="\n".join(f"- {e}" for e in errors), previous=spec.model_dump_json(indent=1)
        )
        spec = await llm.structured(Role.REASONING, MODEL_INSTRUCTIONS, repair, SimSpec)
        errors = validate_spec(spec)
    if errors:
        raise ApplicationError(
            f"SimSpec failed validation after {MAX_REPAIRS} self-repairs: " + "; ".join(errors),
            non_retryable=True,
        )
    for c in unmatched_clarifications(spec, inp.clarifications):
        log.warning("clarification %r = %s %s not found among spec parameters/initial values",
                    c.question, c.answer, c.unit)
    return spec
