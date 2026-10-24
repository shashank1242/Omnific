"""Kubernetes Job sandbox backend (SANDBOX_BACKEND=k8s).

Implements docs/deployment.md, "Sandbox data path on Kubernetes": inputs go into a ConfigMap mounted
read-only at /work/in, the harness prints the result npz base64-encoded between markers on stdout, and
the worker reads it back from the pod log. The runner has no network and no service-account token.
"""

import base64
import binascii
import logging
import re
import time
import uuid
from pathlib import Path

from .backends import BackendResult, _result, truncate

log = logging.getLogger(__name__)

RESULT_BEGIN = "-----OMNIFIC-RESULT-BEGIN-----"
RESULT_END = "-----END-----"
STDERR_MARKER = "-----OMNIFIC-STDERR-----"
MAX_CONFIGMAP_BYTES = 900 * 1024  # ConfigMaps are capped at 1 MiB; leave headroom for metadata
# 6 MiB of npz is ~8 MiB of base64, under the kubelet default containerLogMaxSize (10Mi)
MAX_RESULT_BYTES = 6 * 1024 * 1024
DEADLINE_GRACE_S = 30
_RESULT_RE = re.compile(
    re.escape(RESULT_BEGIN) + r"\s*(?P<b64>[A-Za-z0-9+/=\s]*?)\s*" + re.escape(RESULT_END))


def parse_pod_log(log_text: str) -> tuple[str, str, bytes | None]:
    """Split a combined pod log into (stdout, stderr, result_bytes).

    The base64 block is removed from stdout; text after the STDERR marker is the harness's stderr."""
    result: bytes | None = None
    m = _RESULT_RE.search(log_text)
    if m:
        try:
            result = base64.b64decode(re.sub(r"\s+", "", m.group("b64")), validate=True)
        except (binascii.Error, ValueError):
            result = None
        log_text = log_text[: m.start()] + log_text[m.end():]
    out, _, err = log_text.partition(STDERR_MARKER)
    return out.strip("\n"), err.strip("\n"), result


class K8sJobBackend:
    def __init__(self, namespace: str, image: str, runtime_class: str = "gvisor",
                 core=None, batch=None, poll_interval_s: float = 2.0, sleep=time.sleep):
        self.namespace = namespace
        self.image = image
        self.runtime_class = runtime_class
        self._core = core
        self._batch = batch
        self.poll_interval_s = poll_interval_s
        self._sleep = sleep

    def _clients(self):
        if self._core is None or self._batch is None:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()  # local development
            self._core, self._batch = client.CoreV1Api(), client.BatchV1Api()
        return self._core, self._batch

    def job_manifest(self, name: str, timeout_s: int) -> dict:
        run_id = name.removeprefix("omnific-run-")
        labels = {"app": "omnific-sandbox-run", "omnific.io/run": run_id}
        return {
            "apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": name, "namespace": self.namespace, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": int(timeout_s),
                "ttlSecondsAfterFinished": 300,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "runtimeClassName": self.runtime_class,
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "nodeSelector": {"omnific.io/pool": "sandbox"},
                        "tolerations": [{"key": "sandbox.gke.io/runtime", "operator": "Equal",
                                         "value": "gvisor", "effect": "NoSchedule"}],
                        "securityContext": {
                            "runAsNonRoot": True, "runAsUser": 65534, "runAsGroup": 65534,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [{
                            "name": "runner",
                            "image": self.image,
                            "args": ["/work/in", "/work/out"],
                            "workingDir": "/tmp",
                            "env": [{"name": "OMNIFIC_EMIT_RESULT", "value": "stdout"},
                                    {"name": "HOME", "value": "/tmp"},
                                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
                            "securityContext": {
                                "readOnlyRootFilesystem": True,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "1", "memory": "1Gi",
                                             "ephemeral-storage": "256Mi"},
                                "limits": {"cpu": "2", "memory": "2Gi", "ephemeral-storage": "1Gi"},
                            },
                            "volumeMounts": [
                                {"name": "in", "mountPath": "/work/in", "readOnly": True},
                                {"name": "out", "mountPath": "/work/out"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }],
                        "volumes": [
                            {"name": "in", "configMap": {"name": name}},
                            {"name": "out", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}},
                            {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}},
                        ],
                    },
                },
            },
        }

    def configmap_manifest(self, name: str, files: dict[str, str], job_uid: str) -> dict:
        return {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {
                "name": name, "namespace": self.namespace,
                "labels": {"app": "omnific-sandbox-run",
                           "omnific.io/run": name.removeprefix("omnific-run-")},
                "ownerReferences": [{
                    "apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": job_uid,
                    "blockOwnerDeletion": True, "controller": False,
                }],
            },
            "data": files,
        }

    def _wait(self, batch, name: str, timeout_s: int) -> str:
        """Return 'succeeded', 'failed', 'deadline' (Job hit its deadline) or 'timeout' (we gave up)."""
        end = time.monotonic() + timeout_s + DEADLINE_GRACE_S
        while True:
            st = batch.read_namespaced_job_status(name, self.namespace).status
            if st is not None:
                if getattr(st, "succeeded", None):
                    return "succeeded"
                if getattr(st, "failed", None):
                    reasons = {getattr(c, "reason", None) for c in (st.conditions or [])}
                    return "deadline" if "DeadlineExceeded" in reasons else "failed"
            if time.monotonic() >= end:
                return "timeout"
            self._sleep(self.poll_interval_s)

    def _logs(self, core, name: str) -> str:
        pods = core.list_namespaced_pod(self.namespace, label_selector=f"job-name={name}").items
        if not pods:
            return ""
        return core.read_namespaced_pod_log(pods[0].metadata.name, self.namespace,
                                            container="runner") or ""

    def run(self, in_dir: Path, out_dir: Path, timeout_s: int) -> BackendResult:
        t0 = time.monotonic()
        files = {f.name: f.read_text(encoding="utf-8")
                 for f in sorted(in_dir.iterdir()) if f.is_file()}
        total = sum(len(v.encode("utf-8")) for v in files.values())
        if total > MAX_CONFIGMAP_BYTES:
            return BackendResult(
                False, "", f"input too large for a ConfigMap: {total} bytes exceeds the "
                f"{MAX_CONFIGMAP_BYTES} byte limit (solver code is capped on Kubernetes)",
                time.monotonic() - t0)
        core, batch = self._clients()
        name = f"omnific-run-{uuid.uuid4().hex[:8]}"
        job_created = cm_created = False
        try:
            job = batch.create_namespaced_job(self.namespace, self.job_manifest(name, timeout_s))
            job_created = True
            # The ConfigMap is created after the Job so it can carry the Job's ownerReference (the
            # worker's Role has no `patch`); the pod's volume mount retries until it exists.
            core.create_namespaced_config_map(
                self.namespace, self.configmap_manifest(name, files, job.metadata.uid))
            cm_created = True
            state = self._wait(batch, name, timeout_s)
            try:
                log_text = self._logs(core, name)
            except Exception as e:  # noqa: BLE001
                log.warning("could not read pod log for %s: %r", name, e)
                log_text = ""
            out, err, data = parse_pod_log(log_text)
            dt = time.monotonic() - t0
            if state in ("deadline", "timeout"):
                err = (err + f"\nTimeoutError: run timed out (timeout_s={timeout_s})").strip()
                return BackendResult(False, truncate(out), truncate(err), dt)
            if state == "succeeded" and data is not None:
                if len(data) > MAX_RESULT_BYTES:
                    return BackendResult(False, truncate(out), "result.npz exceeds the 6 MiB limit",
                                         dt)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "result.npz").write_bytes(data)
                return _result(True, out, err, t0, out_dir)
            if state == "succeeded":
                err = err or "run failed: result block missing or corrupt in pod log"
            elif not err:
                err = "run failed: Job failed without error output"
            return BackendResult(False, truncate(out), truncate(err), dt)
        finally:
            if job_created:
                try:
                    batch.delete_namespaced_job(name, self.namespace,
                                                propagation_policy="Background")
                except Exception:
                    log.exception("failed to delete Job %s", name)
            if cm_created:
                try:
                    core.delete_namespaced_config_map(name, self.namespace)
                except Exception:
                    log.exception("failed to delete ConfigMap %s", name)
