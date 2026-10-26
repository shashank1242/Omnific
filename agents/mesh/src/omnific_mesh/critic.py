"""Vision critic for generated meshes, rig poses and animation frames (MODEL_GENERAL, OpenAI vision).

Runs only after the deterministic checks of a stage pass, like the evaluator's LLM critic.
"""

from __future__ import annotations

import logging

from omnific_common.contracts import CheckResult
from omnific_common.llm import LLM, Role
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

PASS_SCORE = 6


class Verdict(BaseModel):
    matches: bool = Field(description="Does the render show what was asked for?")
    score: int = Field(description="0-10 overall quality for a physics-simulation viewer (10 = clean and clear)")
    defects: list[str] = Field(description="Concrete visible problems; empty if none")
    critique: str = Field(description="One or two sentences telling the generator what to change next time")


INSTRUCTIONS = """\
You review 3D assets for a physics-simulation viewer. You see flat-shaded renders (no fancy lighting), so judge
shape, proportions, colours and plausibility, not render quality. Be strict about: the wrong object, missing or extra
parts, broken or fused geometry, floating fragments, inside-out or shredded surfaces, and (for posed or animated
frames) limbs that bend the wrong way, tear, collapse or intersect badly. Minor stylisation is fine."""

PROMPTS = {
    "generate": "Contact sheet: front, side, three-quarter and top views of ONE generated asset.\n"
                "It should be: {what}.",
    "ml_rig": "Contact sheet: the same rigged asset at rest and with individual bones bent ±30°.\n"
              "Asset: {what}. Check the mesh deforms like a plausible joint (no tearing or collapsing).",
    "animate": "Frame strip of one animation clip, left to right in time.\nAsset: {what}. "
               "The clip should read as: {motion}.",
}


async def critique(llm: LLM, stage: str, sheet_png: bytes, what: str, motion: str = "") -> tuple[CheckResult, str]:
    """Returns (check, critique text). A critic error fails open with a note: the deterministic checks already
    passed and a flaky vision call must not throw away a good asset."""
    text = PROMPTS[stage].format(what=what, motion=motion)
    try:
        v = await llm.structured_images(Role.GENERAL, INSTRUCTIONS, text, [(sheet_png, "image/png")], Verdict)
    except Exception as e:  # noqa: BLE001
        log.warning("vision critic unavailable: %s", e)
        return CheckResult(name=f"critic.{stage}", passed=True,
                           detail=f"critic unavailable ({type(e).__name__}); deterministic checks only"), ""
    ok = v.matches and v.score >= PASS_SCORE
    detail = f"score {v.score}/10, {'matches' if v.matches else 'does NOT match'}"
    if v.defects:
        detail += "; defects: " + "; ".join(v.defects[:5])
    return CheckResult(name=f"critic.{stage}", passed=ok, detail=detail), v.critique
