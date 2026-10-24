import base64
import os
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import numpy as np
import pytest
from omnific_sandbox import backends, k8s_backend
from omnific_sandbox.backends import HARNESS_PATH
from omnific_sandbox.k8s_backend import (
    RESULT_BEGIN,
    RESULT_END,
    STDERR_MARKER,
    K8sJobBackend,
    parse_pod_log,
)
from test_sandbox import GOOD, make_spec


def b64block(data: bytes) -> str:
    return f"{RESULT_BEGIN}\n{base64.b64encode(data).decode()}\n{RESULT_END}\n"


def make(tmp_path, job_status, log_text=""):
    core, batch = MagicMock(), MagicMock()
    batch.create_namespaced_job.return_value = NS(metadata=NS(uid="uid-123"))
    batch.read_namespaced_job_status.return_value = NS(status=job_status)
    core.list_namespaced_pod.return_value = NS(items=[NS(metadata=NS(name="pod-1"))])
    core.read_namespaced_pod_log.return_value = log_text
    be = K8sJobBackend("ns", "img:1", "gvisor", core=core, batch=batch, sleep=lambda s: None)
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    (in_dir / "spec.json").write_text("{}")
    (in_dir / "solver.py").write_text("x=1")
    (in_dir / "refine").write_text("1")
    return be, core, batch, in_dir, out_dir


def test_specs_have_security_fields(tmp_path):
    be, core, batch, in_dir, out_dir = make(tmp_path, NS(succeeded=1), b64block(b"npz"))
    r = be.run(in_dir, out_dir, 77)
    assert r.ok and (out_dir / "result.npz").read_bytes() == b"npz"
    ns, job = batch.create_namespaced_job.call_args.args
    assert ns == "ns"
    assert job["spec"]["backoffLimit"] == 0 and job["spec"]["activeDeadlineSeconds"] == 77
    assert job["spec"]["ttlSecondsAfterFinished"] == 300
    pod = job["spec"]["template"]["spec"]
    assert pod["runtimeClassName"] == "gvisor"
    assert pod["nodeSelector"] == {"omnific.io/pool": "sandbox"}
    assert pod["tolerations"][0]["key"] == "sandbox.gke.io/runtime"
    assert pod["automountServiceAccountToken"] is False and pod["enableServiceLinks"] is False
    sc = pod["securityContext"]
    assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 65534
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
    c = pod["containers"][0]
    assert c["securityContext"] == {"readOnlyRootFilesystem": True,
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]}}
    assert c["resources"]["limits"]["memory"] == "2Gi" and c["resources"]["requests"]["cpu"] == "1"
    assert "ephemeral-storage" in c["resources"]["limits"]
    assert {"name": "OMNIFIC_EMIT_RESULT", "value": "stdout"} in c["env"]
    mounts = {m["mountPath"]: m.get("readOnly", False) for m in c["volumeMounts"]}
    assert mounts == {"/work/in": True, "/work/out": False, "/tmp": False}
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["out"]["emptyDir"] == {"medium": "Memory", "sizeLimit": "256Mi"}
    assert vols["tmp"]["emptyDir"]["medium"] == "Memory"
    assert job["metadata"]["labels"]["app"] == "omnific-sandbox-run"
    assert "omnific.io/run" in job["metadata"]["labels"]
    _, cm = core.create_namespaced_config_map.call_args.args
    assert set(cm["data"]) == {"spec.json", "solver.py", "refine"}
    ref = cm["metadata"]["ownerReferences"][0]
    assert ref["kind"] == "Job" and ref["uid"] == "uid-123"
    assert ref["name"] == job["metadata"]["name"]
    assert cm["metadata"]["name"] == job["metadata"]["name"]
    assert vols["in"]["configMap"]["name"] == cm["metadata"]["name"]


def test_parse_pod_log():
    log = f"hello\n{b64block(b'abc')}{STDERR_MARKER}\nTraceback boom\n"
    out, err, data = parse_pod_log(log)
    assert data == b"abc" and "OMNIFIC" not in out and out == "hello"
    assert err == "Traceback boom"
    assert parse_pod_log("just text") == ("just text", "", None)
    assert parse_pod_log(f"{RESULT_BEGIN}\n!!!\n{RESULT_END}")[2] is None


def test_failure_surfaces_stderr(tmp_path):
    be, _, _, in_dir, out_dir = make(
        tmp_path, NS(failed=1, conditions=[]), f"partial\n{STDERR_MARKER}\nValueError: boom\n")
    r = be.run(in_dir, out_dir, 5)
    assert not r.ok and r.stdout == "partial" and "ValueError: boom" in r.stderr


def test_oversize_input_refused(tmp_path):
    be, core, batch, in_dir, out_dir = make(tmp_path, NS(succeeded=1))
    (in_dir / "solver.py").write_text("x" * (901 * 1024))
    r = be.run(in_dir, out_dir, 5)
    assert not r.ok and "too large" in r.stderr
    batch.create_namespaced_job.assert_not_called()
    core.create_namespaced_config_map.assert_not_called()


def test_deadline_exceeded(tmp_path):
    cond = NS(reason="DeadlineExceeded")
    be, _, _, in_dir, out_dir = make(tmp_path, NS(failed=1, conditions=[cond]))
    r = be.run(in_dir, out_dir, 5)
    assert not r.ok and "timed out" in r.stderr


def test_wait_gives_up_and_cleans_up(tmp_path, monkeypatch):
    be, _, batch, in_dir, out_dir = make(tmp_path, NS())  # never finishes
    t = iter(range(0, 10_000, 40))
    monkeypatch.setattr(k8s_backend.time, "monotonic", lambda: next(t))
    r = be.run(in_dir, out_dir, 5)
    assert not r.ok and "timed out" in r.stderr
    name = batch.create_namespaced_job.call_args.args[1]["metadata"]["name"]
    batch.delete_namespaced_job.assert_called_once_with(name, "ns", propagation_policy="Background")


def test_deleted_in_finally_on_error(tmp_path):
    be, core, batch, in_dir, out_dir = make(tmp_path, NS(succeeded=1))
    batch.read_namespaced_job_status.side_effect = RuntimeError("api down")
    with pytest.raises(RuntimeError):
        be.run(in_dir, out_dir, 5)
    batch.delete_namespaced_job.assert_called_once()
    assert batch.delete_namespaced_job.call_args.kwargs["propagation_policy"] == "Background"
    core.delete_namespaced_config_map.assert_called_once()


def test_missing_result_block(tmp_path):
    be, _, _, in_dir, out_dir = make(tmp_path, NS(succeeded=1), "no block")
    r = be.run(in_dir, out_dir, 5)
    assert not r.ok and "result block" in r.stderr


def test_backend_from_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "k8s")
    monkeypatch.setenv("SANDBOX_IMAGE", "r/img:1")
    monkeypatch.setenv("SANDBOX_NAMESPACE", "omnific-sandbox-runs")
    monkeypatch.setenv("SANDBOX_RUNTIME_CLASS", "gvisor")
    be = backends.backend_from_env()
    assert isinstance(be, K8sJobBackend) and be.image == "r/img:1"


def _run_harness(tmp_path, code, emit=True):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "spec.json").write_text(make_spec().model_dump_json())
    (in_dir / "solver.py").write_text(code)
    env = dict(os.environ)
    env.pop("OMNIFIC_EMIT_RESULT", None)
    if emit:
        env["OMNIFIC_EMIT_RESULT"] = "stdout"
    p = subprocess.run([sys.executable, str(HARNESS_PATH), str(in_dir), str(out_dir)],
                       capture_output=True, text=True, env=env, cwd=tmp_path, check=False)
    return p, out_dir


def test_harness_emit_roundtrip(tmp_path):
    from omnific_sim.result import SimResult

    p, _ = _run_harness(tmp_path, GOOD)
    assert p.returncode == 0, p.stderr
    _, err, data = parse_pod_log(p.stdout + p.stderr)
    assert data is not None and err == ""
    f = tmp_path / "r.npz"
    f.write_bytes(data)
    assert np.isclose(SimResult.load_npz(str(f)).state["y"][-1], 10.0 - 0.5 * 9.81)


def test_harness_emit_error_marker(tmp_path):
    p, _ = _run_harness(tmp_path, "def simulate(spec):\n    raise ValueError('boom')\n")
    assert p.returncode != 0
    _, err, data = parse_pod_log(p.stdout + p.stderr)
    assert data is None and "ValueError: boom" in err


def test_harness_no_emit_by_default(tmp_path):
    p, out_dir = _run_harness(tmp_path, GOOD, emit=False)
    assert p.returncode == 0 and RESULT_BEGIN not in p.stdout
    assert (out_dir / "result.npz").exists()


def test_harness_rejects_oversize(tmp_path):
    code = (
        "import numpy as np\n"
        "from omnific_sim.result import SimResult\n"
        "def simulate(spec):\n"
        "    t = np.linspace(spec.time.start, spec.time.end, spec.time.output_samples)\n"
        "    rng = np.random.default_rng(0)\n"
        "    return SimResult(t=t, state={'x': 0 * t, 'y': 0 * t, 'big': rng.random((len(t), 40000))},\n"
        "                     units={'x': 'm', 'y': 'm', 'big': 'm'})\n"
    )
    p, _ = _run_harness(tmp_path, code)
    assert RESULT_BEGIN not in p.stdout
    assert p.returncode == 3 and "limit" in p.stderr
