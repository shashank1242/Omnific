import pytest

pytest.importorskip("httpx")

from test_api import setup  # noqa: F401


def upload(tc, name, data=b"x", mime="application/octet-stream"):
    return tc.post("/inputs", files={"file": (name, data, mime)})


def test_upload_image_and_audio(setup):  # noqa: F811
    tc, _, store = setup
    r = upload(tc, "../my ramp!.PNG", b"PNG", "image/png")
    assert r.status_code == 201 and r.json()["kind"] == "image"
    input_id = r.json()["input_id"]
    assert store.get(f"inputs/{input_id}/my_ramp_.PNG") == b"PNG"
    r = upload(tc, "voice.webm", b"WEBM", "video/webm")  # Chrome's MediaRecorder label
    assert r.status_code == 201 and r.json()["kind"] == "audio"


def test_upload_rejections(setup, monkeypatch):  # noqa: F811
    tc, _, _ = setup
    assert upload(tc, "a.pdf", b"x", "application/pdf").status_code == 415
    assert upload(tc, "a.png", b"x", "text/html").status_code == 415
    assert upload(tc, "a.png", b"").status_code == 422
    monkeypatch.setenv("INPUT_MAX_UPLOAD_MB", "0.001")
    assert upload(tc, "a.wav", b"x" * 2000, "audio/wav").status_code == 413


def test_job_with_inputs(setup):  # noqa: F811
    tc, client, _ = setup
    img = upload(tc, "ramp.png", b"PNG", "image/png").json()["input_id"]
    note = upload(tc, "note.m4a", b"M4A", "audio/x-m4a").json()["input_id"]
    r = tc.post("/jobs", json={"input_ids": [img, note, img]})  # no typed prompt is fine with inputs
    assert r.status_code == 200
    req = client.started[0][1]
    assert req.prompt == ""
    assert [(i.input_id, i.kind, i.mime, i.filename) for i in req.inputs] == [
        (img, "image", "image/png", "ramp.png"), (note, "audio", "audio/mp4", "note.m4a")]
    assert req.inputs[0].key == f"inputs/{img}/ramp.png"


def test_job_input_validation(setup):  # noqa: F811
    tc, client, _ = setup
    assert tc.post("/jobs", json={"prompt": "  "}).status_code == 422
    assert tc.post("/jobs", json={"prompt": "x", "input_ids": ["f" * 32]}).status_code == 422
    assert tc.post("/jobs", json={"prompt": "x", "input_ids": ["../etc"]}).status_code == 422
    assert tc.post("/jobs", json={"prompt": "x", "input_ids": ["a" * 32] * 11}).status_code == 422
    assert client.started == []


def test_mesh_backend(setup, monkeypatch):  # noqa: F811
    tc, client, _ = setup
    tc.post("/jobs", json={"prompt": "x"})
    assert client.started[-1][1].mesh_backend == "procedural"
    monkeypatch.setenv("MESH_BACKEND", "generated")
    tc.post("/jobs", json={"prompt": "x"})
    assert client.started[-1][1].mesh_backend == "generated"
    tc.post("/jobs", json={"prompt": "x", "mesh_backend": "procedural"})
    assert client.started[-1][1].mesh_backend == "procedural"
    assert tc.post("/jobs", json={"prompt": "x", "mesh_backend": "gpu"}).status_code == 422
