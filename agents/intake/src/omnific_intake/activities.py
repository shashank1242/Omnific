import asyncio
import logging

from omnific_common.contracts import (
    ACTIVITY,
    InputRef,
    IntakeInput,
    IntakeItem,
    IntakeOutput,
    TaskQueue,
)
from omnific_common.llm import LLM, Role
from omnific_common.storage import store_from_env
from temporalio import activity

from .prompts import IMAGE_PROMPT, NO_TYPED_PROMPT, PRECEDENCE

log = logging.getLogger(__name__)

llm = LLM()


def _heartbeat(stage: str) -> None:
    try:
        activity.heartbeat(stage)
    except RuntimeError:  # not running inside an activity (unit tests)
        pass


async def _interpret(ref: InputRef, data: bytes) -> str:
    if ref.kind == "image":
        return await llm.describe_image(Role.GENERAL, IMAGE_PROMPT, data, ref.mime)
    return await llm.transcribe(data, ref.filename, ref.mime)


def compose_prompt(prompt: str, items: list[IntakeItem]) -> str:
    parts = [prompt.strip() or NO_TYPED_PROMPT]
    for it in items:
        label = (f"From the sketch/photo `{it.filename}`" if it.kind == "image"
                 else f"From the voice note `{it.filename}` (transcript)")
        parts.append(f"{label}:\n{it.text}")
    if items:
        parts.append(PRECEDENCE)
    return "\n\n".join(parts)


@activity.defn(name=ACTIVITY[TaskQueue.INTAKE])
async def interpret_inputs(inp: IntakeInput) -> IntakeOutput:
    """Never fails over one attachment: an unreadable input becomes a warning and is left out."""
    store = store_from_env()
    items: list[IntakeItem] = []
    warnings: list[str] = []
    for ref in inp.inputs:
        try:
            data = await asyncio.to_thread(store.get, ref.key)
            text = (await _interpret(ref, data)).strip()
        except Exception as e:  # noqa: BLE001 - one bad attachment must not sink the job
            log.warning("could not interpret %s: %s", ref.filename, e)
            warnings.append(f"{ref.filename} could not be interpreted: {type(e).__name__}: {e}")
            continue
        finally:
            _heartbeat(ref.input_id)
        if not text:
            warnings.append(f"{ref.filename} produced no text")
            continue
        items.append(IntakeItem(input_id=ref.input_id, kind=ref.kind, filename=ref.filename, text=text))
    return IntakeOutput(effective_prompt=compose_prompt(inp.prompt, items), items=items, warnings=warnings)
