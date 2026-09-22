"""`apply()` against a fake store: ordering, failure counting, dry run, last_run.json."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import openai
import pytest

from bot import vector_store
from bot.sync import Doc, RunStats, SyncPlan, apply, build_doc, write_last_run
from bot.vector_store import IndexedFile, UploadError
from tests.test_sync_plan import article, doc, indexed


@pytest.fixture(autouse=True)
def words_as_tokens(monkeypatch):
    monkeypatch.setattr(vector_store, "count_tokens", lambda text: len(text.split()))


class FakeStore:
    def __init__(self, *, fail_add=(), fail_remove=()):
        self.calls = []
        self.fail_add = set(fail_add)
        self.fail_remove = set(fail_remove)

    def add(self, path, attrs):
        self.calls.append(("add", path.stem))
        if path.stem in self.fail_add:
            raise UploadError(f"{path.name}: boom")
        return "new_id"

    def replace(self, old, path, attrs):
        self.calls.append(("replace", old.id, path.stem))
        if path.stem in self.fail_add:
            raise UploadError(f"{path.name}: boom")
        return "new_id"

    def remove(self, old):
        self.calls.append(("remove", old.id))
        if old.id in self.fail_remove:
            resp = httpx.Response(500, request=httpx.Request("DELETE", "https://api.test/x"))
            raise openai.InternalServerError("down", response=resp, body=None)


def run(p, store, dry_run=False):
    return apply(p, store, dry_run, chunk_size=800, chunk_overlap=200)


def test_applies_adds_then_updates_then_duplicates_then_removals():
    store = FakeStore()
    p = SyncPlan(
        to_add=[doc(1)],
        to_update=[(doc(2, "sha256:new"), indexed(2, "sha256:old"))],
        to_skip=[doc(3)],
        to_remove=[indexed(4)],
        duplicates=[indexed(2, file_id="f2_dup")],
    )
    stats = run(p, store)
    assert store.calls == [("add", "1-title-1"), ("replace", "f2", "2-title-2"), ("remove", "f2_dup"), ("remove", "f4")]
    assert (stats.added, stats.updated, stats.skipped, stats.removed, stats.failed) == (1, 1, 1, 1, 0)
    assert stats.added_slugs == ["1-title-1"]
    assert stats.updated_slugs == ["2-title-2"]
    assert stats.removed_slugs == ["4-title-4"]  # duplicates are not counted as removals
    assert stats.chunks_embedded == 2


def test_failures_are_counted_and_run_continues():
    store = FakeStore(fail_add={"1-title-1", "2-title-2"}, fail_remove={"f4"})
    p = SyncPlan(
        to_add=[doc(1), doc(5)],
        to_update=[(doc(2, "sha256:new"), indexed(2, "sha256:old"))],
        to_remove=[indexed(4), indexed(6)],
    )
    stats = run(p, store)
    assert (stats.added, stats.updated, stats.removed, stats.failed) == (1, 0, 1, 3)
    assert stats.failed_slugs == ["1-title-1", "2-title-2", "4-title-4"]
    assert stats.chunks_embedded == 1
    assert store.calls[-1] == ("remove", "f6")


def test_dry_run_touches_nothing_but_reports_the_plan(caplog):
    store = FakeStore()
    p = SyncPlan(
        to_add=[doc(1)],
        to_update=[(doc(2, "sha256:new"), indexed(2, "sha256:old"))],
        to_remove=[indexed(4)],
        duplicates=[indexed(2, file_id="f2_dup")],
    )
    with caplog.at_level("INFO", logger="sync"):
        stats = run(p, store, dry_run=True)
    assert store.calls == []
    assert stats.dry_run is True
    assert (stats.added, stats.updated, stats.removed, stats.failed) == (1, 1, 1, 0)
    dry = [r.getMessage() for r in caplog.records if r.getMessage().startswith("DRY RUN")]
    assert len(dry) == 4


def test_chunks_estimated_from_doc_text():
    big = Doc(article=article(1), path=Path("articles/1-x.md"), text=" ".join(["w"] * 1400), content_hash="h")
    stats = run(SyncPlan(to_add=[big]), FakeStore())
    assert stats.chunks_embedded == 2


def test_build_doc_hashes_file_as_written(tmp_path):
    path = tmp_path / "1-title-1.md"
    path.write_text("# Title\n", encoding="utf-8")
    d = build_doc(article(1), path)
    assert d.slug == "1-title-1"
    assert d.content_hash == vector_store.content_hash("# Title\n")
    assert d.attributes() == {
        "article_id": 1,
        "slug": "1-title-1",
        "content_hash": d.content_hash,
        "source_updated_at": "2026-01-01T00:00:00Z",
        "url": "https://example.test/hc/en-us/articles/1-title-1",
    }


def test_summary_line_and_last_run_json(tmp_path):
    stats = RunStats(scraped=5, added=1, updated=2, skipped=2, removed=0, failed=0,
                     chunks_embedded=4, duration_s=41.4, vector_store_id="vs_1",
                     added_slugs=["a"], updated_slugs=["b", "c"])
    line = stats.summary_line()
    assert line.startswith("RUN SUMMARY scraped=5 added=1 updated=2 skipped=2 removed=0 failed=0")
    assert "chunks_embedded≈4 duration=41s" in line

    out = tmp_path / "artifacts" / "last_run.json"
    write_last_run(stats, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["added"] == 1 and data["updated_slugs"] == ["b", "c"]
    assert data["vector_store_id"] == "vs_1"
