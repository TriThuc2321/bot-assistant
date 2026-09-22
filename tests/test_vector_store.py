"""Uploader tests against a fake OpenAI client — no network."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from bot.vector_store import IndexedFile, UploadError, VectorStore, resolve_vector_store


def not_found() -> openai.NotFoundError:
    resp = httpx.Response(404, request=httpx.Request("DELETE", "https://api.test/x"))
    return openai.NotFoundError("not found", response=resp, body=None)


def vs_file(id, created_at=1, status="completed", attributes=None, last_error=None):
    return SimpleNamespace(
        id=id, created_at=created_at, status=status, attributes=attributes, last_error=last_error
    )


def attrs(article_id, **overrides):
    base = {
        "article_id": article_id,
        "slug": f"{article_id}-slug",
        "content_hash": "sha256:abc",
        "source_updated_at": "2026-01-01T00:00:00Z",
        "url": f"https://example.test/hc/en-us/articles/{article_id}",
    }
    base.update(overrides)
    return base


class FakeClient:
    """Records every call in `calls`; canned results are set per test."""

    def __init__(self, *, stores=(), files=(), attach_status="completed", last_error=None,
                 delete_errors=()):
        self.calls = []
        self.stores = list(stores)
        self.store_files = list(files)
        self.attach_status = attach_status
        self.last_error = last_error
        self.delete_errors = set(delete_errors)
        self._n = 0
        c = self
        self.files = SimpleNamespace(create=c._files_create, delete=c._files_delete)
        self.vector_stores = SimpleNamespace(
            retrieve=c._vs_retrieve, list=c._vs_list, create=c._vs_create,
            files=SimpleNamespace(
                list=c._vsf_list, create_and_poll=c._vsf_create_and_poll, delete=c._vsf_delete
            ),
        )

    def _vs_retrieve(self, store_id):
        self.calls.append(("vs.retrieve", store_id))
        return SimpleNamespace(id=store_id, name="retrieved")

    def _vs_list(self):
        self.calls.append(("vs.list",))
        return iter(self.stores)

    def _vs_create(self, name):
        self.calls.append(("vs.create", name))
        return SimpleNamespace(id="vs_new", name=name)

    def _files_create(self, file, purpose):
        name, fh = file
        self.calls.append(("files.create", name, purpose, fh.read()))
        self._n += 1
        return SimpleNamespace(id=f"file_{self._n}")

    def _files_delete(self, file_id):
        self.calls.append(("files.delete", file_id))
        if ("files.delete", file_id) in self.delete_errors:
            raise not_found()

    def _vsf_list(self, store_id):
        self.calls.append(("vsf.list", store_id))
        return iter(self.store_files)

    def _vsf_create_and_poll(self, file_id, *, vector_store_id, attributes, chunking_strategy):
        self.calls.append(("vsf.create_and_poll", file_id, vector_store_id, attributes, chunking_strategy))
        return vs_file(file_id, status=self.attach_status, last_error=self.last_error)

    def _vsf_delete(self, file_id, *, vector_store_id):
        self.calls.append(("vsf.delete", file_id, vector_store_id))
        if ("vsf.delete", file_id) in self.delete_errors:
            raise not_found()


def names(client):
    return [c[0] for c in client.calls]


# --- resolve_vector_store ---------------------------------------------------

def test_resolve_by_id_retrieves_and_never_lists():
    client = FakeClient()
    assert resolve_vector_store(client, "vs_123", "ignored") == "vs_123"
    assert names(client) == ["vs.retrieve"]


def test_resolve_by_name_finds_existing():
    client = FakeClient(stores=[SimpleNamespace(id="vs_a", name="other"), SimpleNamespace(id="vs_b", name="kb")])
    assert resolve_vector_store(client, None, "kb") == "vs_b"
    assert names(client) == ["vs.list"]


def test_resolve_by_name_creates_when_missing():
    client = FakeClient(stores=[SimpleNamespace(id="vs_a", name="other")])
    assert resolve_vector_store(client, "", "kb") == "vs_new"
    assert client.calls[-1] == ("vs.create", "kb")


# --- listing ---------------------------------------------------------------

def test_list_files_parses_attributes_and_keeps_foreign_files():
    client = FakeClient(files=[
        vs_file("f1", attributes=attrs("1")),
        vs_file("f2", attributes=None),
        vs_file("f3", attributes={"something": "else"}),
    ])
    files = VectorStore(client, "vs_1").list_files()
    assert files[0] == IndexedFile(
        id="f1", article_id="1", slug="1-slug", content_hash="sha256:abc",
        source_updated_at="2026-01-01T00:00:00Z",
        url="https://example.test/hc/en-us/articles/1", created_at=1, status="completed",
    )
    assert [f.article_id for f in files] == ["1", "", ""]


# --- add / remove / replace ---------------------------------------------------

@pytest.fixture
def md_file(tmp_path):
    p = tmp_path / "1-slug.md"
    p.write_text("# Title\n\nbody\n", encoding="utf-8")
    return p


def test_add_uploads_with_filename_attributes_and_static_chunking(md_file):
    client = FakeClient()
    store = VectorStore(client, "vs_1", chunk_size=500, chunk_overlap=50)
    a = {**attrs("1"), "article_id": 1, "url": "u" * 600}

    assert store.add(md_file, a) == "file_1"

    assert client.calls[0] == ("files.create", "1-slug.md", "assistants", b"# Title\n\nbody\n")
    kind, file_id, vs_id, sent_attrs, strategy = client.calls[1]
    assert (kind, file_id, vs_id) == ("vsf.create_and_poll", "file_1", "vs_1")
    assert sent_attrs["article_id"] == "1"  # coerced to str
    assert len(sent_attrs["url"]) == 512  # API limit
    assert strategy == {"type": "static", "static": {"max_chunk_size_tokens": 500, "chunk_overlap_tokens": 50}}


def test_add_failed_status_cleans_up_and_raises(md_file):
    client = FakeClient(attach_status="failed", last_error=SimpleNamespace(code="server_error", message="boom"))
    store = VectorStore(client, "vs_1")

    with pytest.raises(UploadError, match="server_error: boom"):
        store.add(md_file, attrs("1"))

    assert names(client) == ["files.create", "vsf.create_and_poll", "vsf.delete", "files.delete"]
    assert client.calls[-1] == ("files.delete", "file_1")


def test_add_api_error_during_attach_deletes_uploaded_file(md_file):
    client = FakeClient()

    def explode(*a, **k):
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.test/x"))

    client.vector_stores.files.create_and_poll = explode
    with pytest.raises(UploadError):
        VectorStore(client, "vs_1").add(md_file, attrs("1"))
    assert names(client) == ["files.create", "files.delete"]


def test_remove_deletes_both_and_tolerates_404():
    client = FakeClient(delete_errors=[("vsf.delete", "f1"), ("files.delete", "f1")])
    old = IndexedFile("f1", "1", "1-slug", "sha256:abc", "", "", 1, "completed")
    VectorStore(client, "vs_1").remove(old)
    assert client.calls == [("vsf.delete", "f1", "vs_1"), ("files.delete", "f1")]


def test_replace_uploads_new_before_deleting_old(md_file):
    client = FakeClient()
    old = IndexedFile("f_old", "1", "1-slug", "sha256:old", "", "", 1, "completed")

    assert VectorStore(client, "vs_1").replace(old, md_file, attrs("1")) == "file_1"

    assert names(client) == ["files.create", "vsf.create_and_poll", "vsf.delete", "files.delete"]
    assert client.calls[2] == ("vsf.delete", "f_old", "vs_1")
    assert client.calls[3] == ("files.delete", "f_old")
