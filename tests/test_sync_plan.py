"""`plan()` is pure: add / update / skip / remove / duplicate / safety guards."""

from __future__ import annotations

from pathlib import Path

from bot.sync import Doc, plan
from bot.vector_store import IndexedFile
from bot.zendesk import Article


def article(id: int) -> Article:
    return Article(
        id=id, title=f"Title {id}", body_html="<p>x</p>",
        html_url=f"https://example.test/hc/en-us/articles/{id}-title-{id}",
        updated_at="2026-01-01T00:00:00Z", section_id=None, section_name=None, label_names=[],
    )


def doc(id: int, h: str = "sha256:same") -> Doc:
    return Doc(article=article(id), path=Path(f"articles/{id}-title-{id}.md"), text="x", content_hash=h)


def indexed(id: int, h: str = "sha256:same", *, file_id=None, created_at=1, status="completed") -> IndexedFile:
    return IndexedFile(
        id=file_id or f"f{id}", article_id=str(id), slug=f"{id}-title-{id}", content_hash=h,
        source_updated_at="", url="", created_at=created_at, status=status,
    )


def test_new_article_is_added():
    p = plan([doc(1)], [])
    assert [d.article_id for d in p.to_add] == ["1"]
    assert not (p.to_update or p.to_skip or p.to_remove or p.duplicates)


def test_same_hash_is_skipped():
    p = plan([doc(1)], [indexed(1)])
    assert [d.article_id for d in p.to_skip] == ["1"]
    assert not (p.to_add or p.to_update or p.to_remove)


def test_different_hash_is_updated_with_old_file():
    p = plan([doc(1, "sha256:new")], [indexed(1, "sha256:old")])
    assert [(d.article_id, old.id) for d, old in p.to_update] == [("1", "f1")]
    assert not (p.to_add or p.to_skip)


def test_non_completed_indexed_file_is_updated_even_with_same_hash():
    p = plan([doc(1)], [indexed(1, status="failed")])
    assert [old.id for _, old in p.to_update] == ["f1"]


def test_missing_article_is_removed_when_scrape_is_healthy():
    p = plan([doc(1), doc(2)], [indexed(1), indexed(2), indexed(3)])
    assert [f.id for f in p.to_remove] == ["f3"]
    assert p.removals_blocked is False


def test_duplicate_keeps_newest_and_removes_the_rest():
    files = [
        indexed(1, "sha256:same", file_id="f_new", created_at=20),
        indexed(1, "sha256:stale", file_id="f_old", created_at=10),
        indexed(1, "sha256:stale", file_id="f_mid", created_at=15),
    ]
    p = plan([doc(1)], files)
    assert [d.article_id for d in p.to_skip] == ["1"]
    assert sorted(f.id for f in p.duplicates) == ["f_mid", "f_old"]
    assert not p.to_remove


def test_empty_scrape_never_deletes():
    p = plan([], [indexed(1), indexed(2)])
    assert p.to_remove == []
    assert p.removals_blocked is True


def test_scrape_below_half_of_index_never_deletes():
    p = plan([doc(1)], [indexed(1), indexed(2), indexed(3)])
    assert p.to_remove == []
    assert p.removals_blocked is True
    assert [d.article_id for d in p.to_skip] == ["1"]


def test_exactly_half_is_allowed():
    p = plan([doc(1), doc(2)], [indexed(1), indexed(2), indexed(3), indexed(4)])
    assert sorted(f.id for f in p.to_remove) == ["f3", "f4"]


def test_incomplete_scrape_blocks_removals_but_not_duplicates():
    files = [indexed(1, file_id="f_new", created_at=2), indexed(1, file_id="f_old", created_at=1), indexed(2)]
    p = plan([doc(1)], files, scrape_complete=False)
    assert p.to_remove == []
    assert p.removals_blocked is True
    assert [f.id for f in p.duplicates] == ["f_old"]


def test_nothing_missing_means_not_blocked():
    p = plan([doc(1)], [indexed(1)], scrape_complete=False)
    assert p.removals_blocked is False


def test_foreign_files_are_ignored_not_removed():
    foreign = IndexedFile("fx", "", "", "", "", "", 1, "completed")
    p = plan([doc(1)], [foreign, indexed(1)])
    assert p.to_remove == [] and p.duplicates == []
    assert [d.article_id for d in p.to_skip] == ["1"]


def test_order_follows_input_order():
    docs = [doc(3), doc(1, "sha256:new"), doc(2)]
    files = [indexed(1, "sha256:old"), indexed(9), indexed(8)]
    p = plan(docs, files)
    assert [d.article_id for d in p.to_add] == ["3", "2"]
    assert [d.article_id for d, _ in p.to_update] == ["1"]
    assert [f.id for f in p.to_remove] == ["f9", "f8"]
