import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
from omnific_common.contracts import ACTIVITY, CheckResult, EvalInput, EvalReport, TaskQueue
from omnific_common.llm import LLM, Role
from omnific_common.storage import store_from_env
from omnific_sim.result import SimResult
from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import checks
from .prompts import CRITIC_INSTRUCTIONS

log = logging.getLogger(__name__)

get_store = store_from_env
llm = LLM()

if os.environ.get("MODEL_GENERAL") and os.environ.get("MODEL_GENERAL") == os.environ.get("MODEL_REASONING"):
    log.warning(
        "MODEL_GENERAL == MODEL_REASONING (%s): the critic should use a different model than the "
        "Solver-coder", os.environ["MODEL_GENERAL"],
    )


class CriticIssue(BaseModel):
    severity: Literal["minor", "major"]
    blame: Literal["modeler", "solver_coder"]
    detail: str


class CriticReport(BaseModel):
    """Agent-local structured output of the LLM critic (not a cross-service contract)."""

    summary: str
    issues: list[CriticIssue]


def critic_enabled() -> bool:
    return os.environ.get("EVAL_CRITIC", "on").strip().lower() not in ("off", "0", "false", "no")


def _num(x: float) -> str:
    return f"{x:.6g}"


def numeric_summary(inp: EvalInput, result: SimResult) -> str:
    lines = [f"t: {_num(result.t[0])} .. {_num(result.t[-1])} {inp.spec.time.unit}, {len(result.t)} samples"]
    for s in inp.spec.state:
        if s.name not in result.state:
            lines.append(f"{s.name}: MISSING")
            continue
        v = checks.values_in_spec_unit(inp.spec, result, s.name)
        extra = f" (field, shape {v.shape[1:]}; initial/final are spatial means)" if v.ndim > 1 else ""
        first, last = v[0], v[-1]
        lines.append(
            f"{s.name} [{s.unit}]: min={_num(np.nanmin(v))} max={_num(np.nanmax(v))} "
            f"initial={_num(np.mean(first))} final={_num(np.mean(last))}{extra}"
        )
    return "\n".join(lines)


def render_critic_input(inp: EvalInput, result: SimResult, results: list[CheckResult]) -> str:
    det = "\n".join(f"- {c.name}: {'PASS' if c.passed else 'FAIL'} - {c.detail}" for c in results)
    spec = inp.spec
    spec_view = spec.model_dump_json(
        indent=1,
        include={"title", "domains", "fidelity", "assumptions", "parameters", "state", "equations",
                 "time", "citations"},
    )
    return (
        f"User prompt:\n{inp.prompt}\n\n"
        f"SimSpec (JSON; parameters carry their cited sources):\n{spec_view}\n\n"
        f"Solver code:\n```python\n{inp.code.code}\n```\n\n"
        f"Result summary (values in spec units):\n{numeric_summary(inp, result)}\n\n"
        f"Deterministic check results:\n{det}"
    )


async def critic_hook(inp: EvalInput, result: SimResult, results: list[CheckResult]) -> str:
    """LLM critic (Role.GENERAL). Appends CheckResults to `results` and returns the summary.

    LLM errors propagate so Temporal retries the activity.
    """
    if not critic_enabled():
        results.append(CheckResult(name="critic", passed=True, detail="disabled"))
        return "Critic disabled (EVAL_CRITIC=off)."
    if not all(c.passed for c in results):
        failed = [c.name for c in results if not c.passed]
        return f"Critic skipped because deterministic checks failed: {', '.join(failed)}."
    report = await llm.structured(
        Role.GENERAL, CRITIC_INSTRUCTIONS, render_critic_input(inp, result, results), CriticReport
    )
    for i, issue in enumerate(report.issues):
        major = issue.severity == "major"
        results.append(CheckResult(
            name=f"critic[{i}]", passed=not major,
            detail=issue.detail if major else f"minor: {issue.detail}",
            blame=issue.blame if major else None,
        ))
    return report.summary


def run_checks(inp: EvalInput, result: SimResult, fine: SimResult | None) -> list[CheckResult]:
    spec = inp.spec
    results = checks.check_dimensions(spec)
    results.append(checks.check_finite(result))
    results += checks.check_bounds(spec, result)
    results += checks.check_invariants(spec, result)
    results += checks.check_analytic(spec, result)
    results.append(checks.check_convergence(spec, result, fine))
    return results


def _load(store, key: str) -> SimResult:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "result.npz"
        p.write_bytes(store.get(key))
        return SimResult.load_npz(str(p))


@activity.defn(name=ACTIVITY[TaskQueue.EVALUATOR])
async def evaluate(inp: EvalInput) -> EvalReport:
    store = get_store()
    try:
        result = _load(store, inp.result_key)
    except (ValueError, KeyError, OSError) as e:
        raise ApplicationError(f"unreadable result {inp.result_key}: {e}", non_retryable=True) from e
    fine = None
    if inp.fine_result_key:
        try:
            fine = _load(store, inp.fine_result_key)
        except (ValueError, KeyError, OSError) as e:
            log.warning("unreadable refined result %s: %s", inp.fine_result_key, e)
    results = run_checks(inp, result, fine)
    summary = await critic_hook(inp, result, results)
    return EvalReport(passed=all(c.passed for c in results), checks=results, critic_summary=summary)
