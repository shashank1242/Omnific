import re

from omnific_common.contracts import ClarifyQuestion
from omnific_sim.units import ureg

MAX_QUESTIONS = 6
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _unit_ok(unit: str) -> bool:
    if not unit.strip():
        return False
    try:
        ureg.parse_units(unit)
        return True
    except Exception:  # noqa: BLE001
        return False


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def question_errors(q: ClarifyQuestion) -> list[str]:
    errs = []
    if not ID_RE.match(q.id):
        errs.append(f"{q.id!r}: id must be snake_case")
    if not q.question.strip():
        errs.append(f"{q.id}: empty question")
    if not q.default.strip():
        errs.append(f"{q.id}: empty default")
    if q.kind == "choice":
        if len(set(q.options)) < 2:
            errs.append(f"{q.id}: choice needs at least 2 distinct options")
        if q.default not in q.options:
            errs.append(f"{q.id}: default {q.default!r} must be one of the options")
    elif q.kind == "number":
        if not _unit_ok(q.unit):
            errs.append(f"{q.id}: number needs a pint-parsable unit, got {q.unit!r}")
        if not _is_number(q.default):
            errs.append(f"{q.id}: number default {q.default!r} is not numeric")
    elif q.kind == "boolean" and q.default.lower() not in ("true", "false"):
        errs.append(f"{q.id}: boolean default must be 'true' or 'false'")
    return errs


def validate(questions: list[ClarifyQuestion]) -> list[str]:
    errs = []
    seen = set()
    for q in questions:
        errs += question_errors(q)
        if q.id in seen:
            errs.append(f"{q.id}: duplicate id")
        seen.add(q.id)
    if len(questions) > MAX_QUESTIONS:
        errs.append(f"{len(questions)} questions; at most {MAX_QUESTIONS}")
    return errs


def keep_valid(questions: list[ClarifyQuestion]) -> list[ClarifyQuestion]:
    """Drop invalid and duplicate-id questions; cap the count."""
    out, seen = [], set()
    for q in questions:
        if question_errors(q) or q.id in seen:
            continue
        seen.add(q.id)
        out.append(q)
    return out[:MAX_QUESTIONS]
