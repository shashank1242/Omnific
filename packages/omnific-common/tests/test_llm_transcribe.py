import pytest
from omnific_common.llm import LLM, LLMError


class FakeTranscriptions:
    def __init__(self):
        self.kw = None

    async def create(self, **kw):
        self.kw = kw
        return "  make the ramp icy \n"


class FakeClient:
    def __init__(self):
        self.audio = type("A", (), {"transcriptions": FakeTranscriptions()})()


async def test_transcribe_uses_env_model(monkeypatch):
    monkeypatch.setenv("MODEL_TRANSCRIBE", "stt-model")
    client = FakeClient()
    out = await LLM(client).transcribe(b"WEBM", "note.webm", "audio/webm")
    assert out == "make the ramp icy"
    kw = client.audio.transcriptions.kw
    assert kw["model"] == "stt-model" and kw["file"] == ("note.webm", b"WEBM", "audio/webm")


async def test_transcribe_needs_model(monkeypatch):
    monkeypatch.delenv("MODEL_TRANSCRIBE", raising=False)
    with pytest.raises(LLMError, match="MODEL_TRANSCRIBE"):
        await LLM(FakeClient()).transcribe(b"x", "a.wav", "audio/wav")
