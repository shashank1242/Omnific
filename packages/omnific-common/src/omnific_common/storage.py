"""Artifact storage. S3-compatible in deployments (MinIO locally), filesystem for tests.

Keys are always namespaced by job: use `job_key(job_id, name)`.
"""

import os
from pathlib import Path
from typing import Protocol


def job_key(job_id: str, name: str) -> str:
    return f"jobs/{job_id}/{name}"


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError(f"key escapes store root: {key}")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Store:
    def __init__(self, bucket: str, endpoint_url: str | None = None):
        import boto3

        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint_url)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


class GCSStore:
    """Google Cloud Storage via Application Default Credentials (Workload Identity on GKE)."""

    def __init__(self, bucket: str, client=None):
        if client is None:
            from google.cloud import storage

            client = storage.Client()
        self.bucket = client.bucket(bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.bucket.blob(key).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes:
        return self.bucket.blob(key).download_as_bytes()

    def exists(self, key: str) -> bool:
        return self.bucket.blob(key).exists()


def store_from_env() -> ArtifactStore:
    """ARTIFACT_STORE=gcs (GCS_BUCKET), s3 (S3_BUCKET, optional S3_ENDPOINT_URL) or local (ARTIFACT_DIR)."""
    kind = os.environ.get("ARTIFACT_STORE", "local")
    if kind == "gcs":
        return GCSStore(os.environ["GCS_BUCKET"])
    if kind == "s3":
        return S3Store(os.environ["S3_BUCKET"], os.environ.get("S3_ENDPOINT_URL"))
    return LocalStore(os.environ.get("ARTIFACT_DIR", ".artifacts"))
