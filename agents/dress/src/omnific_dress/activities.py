"""Scene dressing activities: write the Blender scripts (never run here) and judge the Blender runner's output.

The scripts run only in the Blender runner (`run_blender` on the sandbox queue). The evaluation gates on what the
writer controls (its scripts ran, the protected entities are unchanged, scenery stays clear of the motion, the
dressed scene meets the scene.gltf contract, the vision critic) and reports the .blend checks alongside.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

from omnific_common.contracts import (
    CheckResult,
    DressActivity,
    DressDraft,
    DressEvaluateInput,
    DressScript,
    DressScripts,
    DressWriteInput,
    StageEval,
)
from omnific_common.llm import LLM, Role
from omnific_common.storage import store_from_env
from pydantic import BaseModel, Field
from temporalio import activity

from .brief import build_brief
from .prompts import CRITIC_INSTRUCTIONS, CRITIC_PROMPT, RETRY_SUFFIX, WRITER_INSTRUCTIONS

log = logging.getLogger(__name__)

# Module-level hooks so tests can monkeypatch them.
get_store = store_from_env
llm = LLM()

BLENDER_VERSION = "5.0"
ORDER = ("look", "scenery", "lighting")
PASS_SCORE = 6
MAX_ERROR_CHARS = 1500


def _heartbeat(msg: str) -> None:
    try:
        activity.heartbeat(msg)
    except RuntimeError:  # not inside an activity (tests)
        pass


# --- write ------------------------------------------------------------------------------------------------------

def _unfence(code: str) -> str:
    s = code.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip() + "\n"


def normalize_scripts(out: DressDraft | DressScripts, allowed_props: set[str] | None = None) -> DressScripts:
    """One script per role, in run order (the first one wins), code without markdown fences; only props that were
    offered (each once)."""
    first: dict[str, DressScript] = {}
    for s in out.scripts:
        if s.agent not in first:
            first[s.agent] = s.model_copy(update={"code": _unfence(s.code)})
    props = [p for p in dict.fromkeys(out.props) if allowed_props is None or p in allowed_props]
    return DressScripts(scripts=[first[a] for a in ORDER if a in first], props=props)


def render_previous(scripts: list[DressScript]) -> str:
    return "\n\n".join(f"# --- {s.agent} ---\n{s.code}" for s in scripts) or "(none)"


@activity.defn(name=DressActivity.WRITE.value)
async def dress_write(inp: DressWriteInput) -> DressScripts:
    """Only WRITES the scripts: they run in the Blender runner (run_blender), never in this worker."""
    store = get_store()
    playback = json.loads(await asyncio.to_thread(store.get, inp.playback_key))
    gltf = json.loads(await asyncio.to_thread(store.get, inp.scene_key))
    text = build_brief(inp.prompt, inp.spec, playback, gltf, inp.scene_report, inp.prop_options)
    if inp.critique:
        text += RETRY_SUFFIX.format(critique=inp.critique[:4000], previous=render_previous(inp.previous))
    _heartbeat("writing Blender scripts")
    out = await llm.structured(Role.REASONING, WRITER_INSTRUCTIONS.format(blender=BLENDER_VERSION), text,
                               DressDraft)
    return normalize_scripts(out, {o.id for o in inp.prop_options})


# --- evaluate ---------------------------------------------------------------------------------------------------

def report_checks(report: dict, scene_checks: list[CheckResult]) -> list[CheckResult]:
    """Deterministic checks from the harness report. Names starting with `blend.` describe the .blend export and
    don't gate the dressing (the writer can't fix them)."""
    checks: list[CheckResult] = []
    for s in report.get("scripts", []):
        checks.append(CheckResult(name=f"dress.script.{s['name']}", passed=bool(s.get("ok")),
                                  detail="ran" if s.get("ok") else str(s.get("error", ""))[:MAX_ERROR_CHARS]))
    if not report.get("scripts"):
        checks.append(CheckResult(name="dress.scripts", passed=False, detail="no script ran"))
    c = report.get("checks", {})
    prot = c.get("protected")
    checks.append(CheckResult(
        name="dress.protected", passed=bool(prot and prot.get("ok")),
        detail="the entities are untouched" if prot and prot.get("ok") else
        f"a script changed protected entities {(prot or {}).get('changed', [])}: only omnific.restyle may touch them"))
    same = c.get("gltf_protected")
    checks.append(CheckResult(name="dress.entities_unchanged", passed=bool(same and same.get("ok")),
                              detail="entity nodes identical to SceneBuild's" if same and same.get("ok") else
                              "the dressed scene changed an entity node"))
    clear = c.get("clearance")
    checks.append(CheckResult(
        name="dress.clearance", passed=bool(clear and clear.get("ok")),
        detail=f"{(clear or {}).get('scenery_objects', 0)} scenery objects, none in the way" if clear and clear.get("ok")
        else "scenery in the way of the motion: " + "; ".join((clear or {}).get("hits", [])[:8])))
    checks += [c_.model_copy(update={"name": f"dress.{c_.name}"}) for c_ in scene_checks]
    if not scene_checks:  # the dressed scene must meet the scene.gltf contract: no checks means no pass
        checks.append(CheckResult(name="dress.scene", passed=False, detail="the scene.gltf contract was not checked"))
    fid = c.get("fidelity") or {}
    checks.append(CheckResult(name="blend.fidelity", passed=bool(fid.get("ok")),
                              detail=f"max error {fid.get('max_error', float('nan')):.2e} scene units over "
                                     f"{fid.get('frames', 0)} frames ({fid.get('where', '')})"))
    fr = c.get("framing") or {}
    checks.append(CheckResult(name="blend.framing", passed=bool(fr.get("ok")),
                              detail="the default camera keeps every moving object in view" if fr.get("ok")
                              else f"partly out of view: {fr.get('worst', {})}"))
    return checks


class Verdict(BaseModel):
    clear: bool = Field(description="Does the dressing keep the simulated objects clearly visible (not hidden, "
                                    "crowded, camouflaged or left in the dark)? Not about their shape or position")
    score: int = Field(description="0-10 overall: 10 = clean, clear and fitting the setting")
    defects: list[str] = Field(description="Concrete visible problems; empty if none")
    critique: str = Field(description="One or two sentences telling the script writer what to change")


def contact_sheet(images: list[bytes]) -> bytes:
    from PIL import Image

    tiles = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
    w, h = tiles[0].size
    cols = 2 if len(tiles) > 1 else 1
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t.resize((w, h)), ((i % cols) * w, (i // cols) * h))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


async def critique(prompt: str, setting: str, entities: list[str], sheet: bytes) -> tuple[CheckResult, str]:
    """Fails open like the mesh critic: the deterministic checks passed and a flaky vision call must not throw
    away a good scene."""
    try:
        v = await llm.structured_images(
            Role.GENERAL, CRITIC_INSTRUCTIONS,
            CRITIC_PROMPT.format(prompt=prompt, setting=setting, entities=", ".join(entities)),
            [(sheet, "image/png")], Verdict)
    except Exception as e:  # noqa: BLE001
        log.warning("dress critic unavailable: %s", e)
        return CheckResult(name="dress.critic", passed=True,
                           detail=f"critic unavailable ({type(e).__name__}); deterministic checks only"), ""
    ok = v.clear and v.score >= PASS_SCORE
    detail = f"score {v.score}/10, {'clear' if v.clear else 'NOT clear'}"
    if v.defects:
        detail += "; defects: " + "; ".join(v.defects[:5])
    return CheckResult(name="dress.critic", passed=ok, detail=detail), v.critique


@activity.defn(name=DressActivity.EVALUATE.value)
async def dress_evaluate(inp: DressEvaluateInput) -> StageEval:
    run = inp.run
    if not run.ok or not run.report_key:
        err = (run.stderr or "no output").strip()[-MAX_ERROR_CHARS:]
        return StageEval(stage="dress", attempt=inp.attempt, passed=False, checks=[CheckResult(
            name="dress.run", passed=False, detail=f"the Blender runner failed: {err}")],
            critique=f"The Blender run failed before any check: {err}")
    store = get_store()
    report = json.loads(await asyncio.to_thread(store.get, run.report_key))
    checks = report_checks(report, inp.scene_checks)
    gating = [c for c in checks if not c.name.startswith("blend.")]
    text = ""
    if all(c.passed for c in gating):
        stills = [await asyncio.to_thread(store.get, k) for k in run.still_keys]
        if stills:
            _heartbeat("vision critic")
            sheet = await asyncio.to_thread(contact_sheet, stills)
            env = inp.spec.environment
            setting = f"{env.name}: {env.description}" if env else "neutral studio"
            c, text = await critique(inp.prompt, setting, [e.name for e in inp.spec.entities], sheet)
            checks.append(c)
            gating.append(c)
        else:
            checks.append(CheckResult(name="dress.critic", passed=True,
                                      detail="no stills rendered; deterministic checks only"))
    passed = all(c.passed for c in gating)
    if not passed and not text:
        text = "; ".join(f"{c.name}: {c.detail}" for c in gating if not c.passed)
    return StageEval(stage="dress", attempt=inp.attempt, passed=passed, checks=checks, critique=text)


DRESS_ACTIVITIES = [dress_write, dress_evaluate]
