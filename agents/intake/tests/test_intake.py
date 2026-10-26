from omnific_common.contracts import InputRef, IntakeInput
from omnific_common.storage import LocalStore
from omnific_intake import activities
from omnific_intake.prompts import NO_TYPED_PROMPT, PRECEDENCE


class FakeLLM:
    def __init__(self, image="a 2 kg block on a 30° ramp", transcript="make the ramp icy", fail=()):
        self.image, self.transcript, self.fail, self.calls = image, transcript, set(fail), []

    async def describe_image(self, role, prompt, image, mime):
        self.calls.append(("image", mime, image))
        if "image" in self.fail:
            raise RuntimeError("vision down")
        return self.image

    async def transcribe(self, audio, filename, mime):
        self.calls.append(("audio", mime, audio))
        if "audio" in self.fail:
            raise RuntimeError("stt down")
        return self.transcript


def ref(kind, name):
    mime = "image/png" if kind == "image" else "audio/webm"
    return InputRef(input_id=f"id-{name}", key=f"inputs/id-{name}/{name}", kind=kind, mime=mime, filename=name)


def setup(tmp_path, monkeypatch, fake):
    store = LocalStore(tmp_path)
    store.put("inputs/id-ramp.png/ramp.png", b"PNG")
    store.put("inputs/id-note.webm/note.webm", b"WEBM")
    monkeypatch.setattr(activities, "store_from_env", lambda: store)
    monkeypatch.setattr(activities, "llm", fake)


async def test_image_and_audio_become_prompt_sections(tmp_path, monkeypatch):
    fake = FakeLLM()
    setup(tmp_path, monkeypatch, fake)
    out = await activities.interpret_inputs(IntakeInput(
        job_id="j", prompt="simulate this", inputs=[ref("image", "ramp.png"), ref("audio", "note.webm")]))
    assert [c[:2] for c in fake.calls] == [("image", "image/png"), ("audio", "audio/webm")]
    assert fake.calls[0][2] == b"PNG"
    assert [i.kind for i in out.items] == ["image", "audio"] and not out.warnings
    p = out.effective_prompt
    assert p.startswith("simulate this")
    assert "`ramp.png`:\na 2 kg block on a 30° ramp" in p and "make the ramp icy" in p
    assert p.endswith(PRECEDENCE)


async def test_failed_item_is_a_warning(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, FakeLLM(fail={"audio"}))
    out = await activities.interpret_inputs(IntakeInput(
        job_id="j", prompt="", inputs=[ref("image", "ramp.png"), ref("audio", "note.webm"),
                                       ref("image", "missing.png")]))
    assert [i.filename for i in out.items] == ["ramp.png"]
    assert len(out.warnings) == 2 and "note.webm" in out.warnings[0] and "missing.png" in out.warnings[1]
    assert out.effective_prompt.startswith(NO_TYPED_PROMPT)


async def test_empty_text_is_a_warning(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, FakeLLM(image="  "))
    out = await activities.interpret_inputs(IntakeInput(job_id="j", prompt="x", inputs=[ref("image", "ramp.png")]))
    assert out.items == [] and out.warnings == ["ramp.png produced no text"]
    assert out.effective_prompt == "x"
