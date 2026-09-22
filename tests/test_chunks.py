"""`estimate_chunks` edge cases. Token counting is stubbed so tiktoken never downloads."""

from __future__ import annotations

import pytest

from bot import vector_store
from bot.vector_store import content_hash, estimate_chunks


@pytest.fixture(autouse=True)
def words_as_tokens(monkeypatch):
    monkeypatch.setattr(vector_store, "count_tokens", lambda text: len(text.split()))


def doc(n: int) -> str:
    return " ".join(["w"] * n)


def test_short_doc_is_one_chunk():
    assert estimate_chunks(doc(10), 800, 200) == 1
    assert estimate_chunks("", 800, 200) == 1


def test_exact_boundary_is_one_chunk():
    assert estimate_chunks(doc(800), 800, 200) == 1


def test_one_over_boundary_is_two_chunks():
    assert estimate_chunks(doc(801), 800, 200) == 2


def test_long_doc_mirrors_static_strategy():
    # 2000 tokens: chunks advance by (800 - 200) = 600 after the first.
    assert estimate_chunks(doc(2000), 800, 200) == 3
    assert estimate_chunks(doc(2001), 800, 200) == 4


def test_zero_overlap():
    assert estimate_chunks(doc(1600), 800, 0) == 2
    assert estimate_chunks(doc(1601), 800, 0) == 3


def test_content_hash_is_prefixed_and_deterministic():
    h = content_hash("# Title\n\nbody")
    assert h.startswith("sha256:") and len(h) == len("sha256:") + 64
    assert h == content_hash("# Title\n\nbody")
    assert h != content_hash("# Title\n\nbody!")
