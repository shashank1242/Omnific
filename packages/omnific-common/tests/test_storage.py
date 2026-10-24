import types

import pytest
from omnific_common import storage


def test_local_store_roundtrip_and_traversal(tmp_path):
    st = storage.LocalStore(tmp_path)
    key = storage.job_key("j1", "a/b.txt")
    st.put(key, b"hi")
    assert st.exists(key) and st.get(key) == b"hi"
    with pytest.raises(ValueError):
        st.put("../escape", b"x")


class _FakeGCS:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def bucket(self, name):
        blobs = self.blobs

        class Blob:
            def __init__(self, key):
                self.key = key

            def upload_from_string(self, data, content_type=None):
                blobs[self.key] = data

            def download_as_bytes(self):
                return blobs[self.key]

            def exists(self):
                return self.key in blobs

        return types.SimpleNamespace(name=name, blob=Blob)


def test_gcs_store_roundtrip():
    st = storage.GCSStore("bkt", client=_FakeGCS())
    st.put("k", b"v")
    assert st.exists("k") and st.get("k") == b"v" and not st.exists("other")


def test_store_from_env_selects_gcs(monkeypatch):
    monkeypatch.setenv("ARTIFACT_STORE", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "bkt")
    monkeypatch.setattr(storage, "GCSStore", lambda bucket: ("gcs", bucket))
    assert storage.store_from_env() == ("gcs", "bkt")
