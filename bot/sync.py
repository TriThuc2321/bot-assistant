"""Delta detection & state (spec 04).

The vector store's per-file attributes (`article_id`, `content_hash`) *are* the sync
state: each run lists the store and compares against the freshly generated Markdown.
sha256 of the Markdown is the change signal, so metadata-only edits are skipped and
converter changes re-upload every affected article (desirable).

`plan()` is pure; `apply()` does the I/O. Updates upload the new version before
deleting the old one so an article is never missing from the store mid-run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import openai

from bot.vector_store import IndexedFile, UploadError, VectorStore, content_hash, estimate_chunks
from bot.zendesk import Article

log = logging.getLogger("sync")

# Below this fraction of the indexed count the scrape is assumed broken, not shrunk.
REMOVE_GUARD_RATIO = 0.5


@dataclass(frozen=True)
class Doc:
    article: Article
    path: Path
    text: str
    content_hash: str

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def article_id(self) -> str:
        return str(self.article.id)

    def attributes(self) -> dict:
        return {
            "article_id": self.article.id,
            "slug": self.slug,
            "content_hash": self.content_hash,
            "source_updated_at": self.article.updated_at,
            "url": self.article.html_url,
        }


def build_doc(article: Article, path: Path) -> Doc:
    """Hash the file as written so the hash matches exactly what gets uploaded."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return Doc(article=article, path=path, text=text, content_hash=content_hash(text))


@dataclass
class SyncPlan:
    to_add: list[Doc] = field(default_factory=list)
    to_update: list[tuple[Doc, IndexedFile]] = field(default_factory=list)
    to_skip: list[Doc] = field(default_factory=list)
    to_remove: list[IndexedFile] = field(default_factory=list)
    duplicates: list[IndexedFile] = field(default_factory=list)
    removals_blocked: bool = False


def plan(docs: list[Doc], files: list[IndexedFile], *, scrape_complete: bool = True) -> SyncPlan:
    """Classify `docs` against the store's files. Pure: no I/O.

    `files` is the raw store listing; newest file per `article_id` wins and older copies
    (left by a crashed run) go to `duplicates`. Files without `article_id` were not
    uploaded by us and are left alone.
    """
    p = SyncPlan()
    index: dict[str, IndexedFile] = {}
    for f in files:
        if not f.article_id:
            log.warning("ignoring file %s: no article_id attribute", f.id)
            continue
        current = index.get(f.article_id)
        if current is None:
            index[f.article_id] = f
        elif f.created_at > current.created_at:
            p.duplicates.append(current)
            index[f.article_id] = f
        else:
            p.duplicates.append(f)

    for d in docs:
        old = index.get(d.article_id)
        if old is None:
            p.to_add.append(d)
        elif old.content_hash != d.content_hash or old.status != "completed":
            p.to_update.append((d, old))
        else:
            p.to_skip.append(d)

    scraped_ids = {d.article_id for d in docs}
    missing = [f for aid, f in index.items() if aid not in scraped_ids]
    if missing:
        if not scrape_complete or not docs or len(docs) < REMOVE_GUARD_RATIO * len(index):
            p.removals_blocked = True
        else:
            p.to_remove = missing
    return p


@dataclass
class RunStats:
    scraped: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0
    chunks_embedded: int = 0
    duration_s: float = 0.0
    dry_run: bool = False
    vector_store_id: str = ""
    finished_at: str = ""
    added_slugs: list[str] = field(default_factory=list)
    updated_slugs: list[str] = field(default_factory=list)
    removed_slugs: list[str] = field(default_factory=list)
    failed_slugs: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        return (
            f"RUN SUMMARY scraped={self.scraped} added={self.added} updated={self.updated} "
            f"skipped={self.skipped} removed={self.removed} failed={self.failed} "
            f"chunks_embedded≈{self.chunks_embedded} duration={self.duration_s:.0f}s "
            f"dry_run={self.dry_run} vector_store={self.vector_store_id}"
        )

    def to_dict(self) -> dict:
        return asdict(self)


def write_last_run(stats: RunStats, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply(
    p: SyncPlan, store: VectorStore, dry_run: bool, *, chunk_size: int, chunk_overlap: int
) -> RunStats:
    """Execute the plan: adds, then updates, then deletes. Each failure is counted, not fatal."""
    stats = RunStats(dry_run=dry_run, skipped=len(p.to_skip))

    for d in p.to_add:
        n = estimate_chunks(d.text, chunk_size, chunk_overlap)
        if dry_run:
            log.info("DRY RUN would add %s (chunks≈%d)", d.slug, n)
        else:
            try:
                store.add(d.path, d.attributes())
            except UploadError as exc:
                stats.failed += 1
                stats.failed_slugs.append(d.slug)
                log.warning("add failed: %s", exc)
                continue
        stats.added += 1
        stats.chunks_embedded += n
        stats.added_slugs.append(d.slug)

    for d, old in p.to_update:
        n = estimate_chunks(d.text, chunk_size, chunk_overlap)
        if dry_run:
            log.info("DRY RUN would update %s (replaces %s, chunks≈%d)", d.slug, old.id, n)
        else:
            try:
                store.replace(old, d.path, d.attributes())
            except (UploadError, openai.APIError) as exc:
                stats.failed += 1
                stats.failed_slugs.append(d.slug)
                log.warning("update failed: %s", exc)
                continue
        stats.updated += 1
        stats.chunks_embedded += n
        stats.updated_slugs.append(d.slug)

    for old in p.duplicates:
        log.warning("duplicate file %s for article %s (%s)", old.id, old.article_id, old.slug)
        _remove(store, old, dry_run, stats, count=False)

    for old in p.to_remove:
        _remove(store, old, dry_run, stats, count=True)

    return stats


def _remove(store: VectorStore, old: IndexedFile, dry_run: bool, stats: RunStats, *, count: bool) -> None:
    label = old.slug or old.id
    if dry_run:
        log.info("DRY RUN would remove %s", label)
    else:
        try:
            store.remove(old)
        except openai.APIError as exc:
            stats.failed += 1
            stats.failed_slugs.append(label)
            log.warning("remove failed for %s: %s", label, exc)
            return
    if count:
        stats.removed += 1
        stats.removed_slugs.append(label)
