"""OpenAI Vector Store uploader & chunking (spec 03).

Uploads `articles/<slug>.md` files to a vector store with per-file attributes that
double as the sync state for spec 04 (`article_id`, `content_hash`, ...). Uses the
Files + Vector Stores API only; `client.beta.assistants` / `threads` were sunset.

Chunking (for the README):
- Static strategy, set explicitly: 800 max tokens, 200 overlap. Help-center articles
  are short, single-topic and step-by-step; most fit in 1-3 chunks of 800 tokens, so a
  step list rarely gets split mid-procedure.
- 200 overlap (vs the API default 400) halves duplicated embeddings while still
  carrying a heading / some context across chunk boundaries.
- The title + `Article URL:` header sits in chunk 1, so retrieving the first chunk
  always yields a citable URL. Later chunks lack it; the converter mitigates this by
  repeating the URL at the end of long documents.
- The API does not report chunk counts. `estimate_chunks` mirrors the configured
  static strategy locally (tiktoken `o200k_base`), so `chunks_estimated` in the run
  summary is an estimate, not an API-reported value.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import openai
from openai import OpenAI

log = logging.getLogger("vector_store")

MAX_ATTR_VALUE_LEN = 512


class UploadError(Exception):
    """One file failed to upload or index. Callers count it and continue the run."""


@dataclass(frozen=True)
class IndexedFile:
    id: str  # vector-store file id == the underlying File id
    article_id: str
    slug: str
    content_hash: str
    source_updated_at: str
    url: str
    created_at: int
    status: str  # in_progress | completed | failed | cancelled


def build_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, max_retries=3)


def resolve_vector_store(client: OpenAI, store_id: str | None, name: str) -> str:
    """Return the store id: `store_id` if given (must exist), else find-or-create by name."""
    if store_id:
        store = client.vector_stores.retrieve(store_id)
    else:
        store = next((s for s in client.vector_stores.list() if s.name == name), None)
        if store is None:
            store = client.vector_stores.create(name=name)
            log.info("created vector store %r", name)
    log.info("vector store id=%s name=%r", store.id, store.name)
    return store.id


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def estimate_chunks(text: str, size: int, overlap: int) -> int:
    n = count_tokens(text)
    if n <= size:
        return 1
    return math.ceil((n - overlap) / (size - overlap))


def _to_indexed(f: Any) -> IndexedFile:
    attrs = f.attributes or {}
    return IndexedFile(
        id=f.id,
        article_id=str(attrs.get("article_id", "")),
        slug=str(attrs.get("slug", "")),
        content_hash=str(attrs.get("content_hash", "")),
        source_updated_at=str(attrs.get("source_updated_at", "")),
        url=str(attrs.get("url", "")),
        created_at=f.created_at,
        status=f.status,
    )


class VectorStore:
    def __init__(self, client: OpenAI, store_id: str, *, chunk_size: int = 800, chunk_overlap: int = 200):
        self.client = client
        self.store_id = store_id
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def _chunking_strategy(self) -> dict:
        return {
            "type": "static",
            "static": {
                "max_chunk_size_tokens": self.chunk_size,
                "chunk_overlap_tokens": self.chunk_overlap,
            },
        }

    def list_files(self) -> list[IndexedFile]:
        """Every file in the store, including ones we did not upload (no `article_id`)."""
        return [_to_indexed(f) for f in self.client.vector_stores.files.list(self.store_id)]

    def list_indexed(self) -> dict[str, IndexedFile]:
        """Files keyed by `article_id`; newest wins when a crashed run left duplicates."""
        index: dict[str, IndexedFile] = {}
        for f in self.list_files():
            if not f.article_id:
                log.warning("ignoring file %s: no article_id attribute", f.id)
                continue
            current = index.get(f.article_id)
            if current is None or f.created_at > current.created_at:
                index[f.article_id] = f
        return index

    def add(self, path: Path, attrs: dict) -> str:
        """Upload `path` and attach it to the store; returns the vector-store file id."""
        path = Path(path)
        attributes = {k: str(v)[:MAX_ATTR_VALUE_LEN] for k, v in attrs.items()}
        file_id: str | None = None
        try:
            with open(path, "rb") as fh:
                file_id = self.client.files.create(file=(path.name, fh), purpose="assistants").id
            vs_file = self.client.vector_stores.files.create_and_poll(
                file_id,
                vector_store_id=self.store_id,
                attributes=attributes,
                chunking_strategy=self._chunking_strategy(),
            )
        except openai.APIError as exc:
            if file_id:
                self._delete_file(file_id)
            raise UploadError(f"{path.name}: {exc}") from exc

        if vs_file.status != "completed":
            err = vs_file.last_error
            detail = f"{err.code}: {err.message}" if err else "no error detail"
            # Leave nothing behind: a failed vs-file still pins the File in storage.
            self._delete_vs_file(file_id)
            self._delete_file(file_id)
            raise UploadError(f"{path.name}: status={vs_file.status} ({detail})")

        log.info("indexed %s as %s", path.name, vs_file.id)
        return vs_file.id

    def replace(self, old: IndexedFile, path: Path, attrs: dict) -> str:
        """Upload the new version first so the article is never missing mid-run."""
        new_id = self.add(path, attrs)
        self.remove(old)
        return new_id

    def remove(self, old: IndexedFile) -> None:
        self._delete_vs_file(old.id)
        self._delete_file(old.id)
        log.info("removed %s (%s)", old.id, old.slug or "no slug")

    # Both deletes tolerate 404 so a half-finished removal from a crashed run can be retried.
    def _delete_vs_file(self, file_id: str) -> None:
        try:
            self.client.vector_stores.files.delete(file_id, vector_store_id=self.store_id)
        except openai.NotFoundError:
            pass

    def _delete_file(self, file_id: str) -> None:
        try:
            self.client.files.delete(file_id)
        except openai.NotFoundError:
            pass
