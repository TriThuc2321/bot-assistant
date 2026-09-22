"""Entrypoint: scrape (spec 01) -> convert to Markdown (spec 02) -> delta sync (spec 04)
against the vector store (spec 03) -> RUN SUMMARY + artifacts/last_run.json."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import openai

from bot.config import ConfigError, load_config
from bot.logging_setup import setup_logging
from bot.markdown import slugify, write_markdown
from bot.sync import apply, build_doc, plan, write_last_run
from bot.vector_store import VectorStore, build_client, resolve_vector_store
from bot.zendesk import Article, ScrapeError, fetch_articles

log = logging.getLogger("main")

LAST_RUN_PATH = Path("artifacts/last_run.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync the help center into an OpenAI vector store.")
    parser.add_argument("--scrape-only", action="store_true", help="write .md files, skip the vector store")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    args = parse_args(argv)
    try:
        cfg = load_config(require_api_key=False)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    setup_logging(cfg.log_level)
    if not args.scrape_only and not cfg.openai_api_key:
        log.error("Missing OPENAI_API_KEY (or API_KEY); pass --scrape-only to run without it")
        return 1

    try:
        articles = fetch_articles(cfg.zendesk_base_url, cfg.zendesk_locale, cfg.max_articles)
    except ScrapeError as exc:
        log.error("%s", exc)
        return 2

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = failed = 0
    converted: list[tuple[Article, Path]] = []
    seen_slugs: set[str] = set()
    for a in articles:
        slug = slugify(a)
        if slug in seen_slugs:
            log.warning(
                "Duplicate slug %r for article %s (%r); this output will overwrite a previous one",
                slug, a.id, a.title,
            )
        seen_slugs.add(slug)
        try:
            path = write_markdown(a, out_dir)
        except Exception:  # one bad article must not kill the run
            failed += 1
            log.warning("Failed to convert article %s (%r)", a.id, a.title, exc_info=True)
            continue
        written += 1
        converted.append((a, path))
        log.info("wrote %s", path)

    # Prune stale .md files left over from a previous run (e.g. renamed/unpublished
    # articles). Only do this when every article converted cleanly, so a partial
    # failure never deletes still-valid content for the articles that failed.
    written_paths = {path for _, path in converted}
    if articles and failed == 0:
        for existing in out_dir.glob("*.md"):
            if existing not in written_paths:
                existing.unlink()
                log.info("removed stale %s", existing)
    elif failed:
        log.warning("skipping stale-file cleanup: %d article(s) failed to convert this run", failed)

    # No usable content came out of the scrape/convert stage, so this is a scrape
    # failure (2), not a vector-store one; 3 is reserved for the store per spec 05.
    if articles and written == 0:
        log.error("all %d article(s) failed to convert; treating run as failed", len(articles))
        return 2
    if args.scrape_only:
        log.info(
            "RUN SUMMARY articles=%d written=%d failed=%d output_dir=%s (scrape only)",
            len(articles), written, failed, out_dir,
        )
        return 0

    docs = [build_doc(a, path) for a, path in converted]
    try:
        client = build_client(cfg.openai_api_key)
        store_id = resolve_vector_store(client, cfg.vector_store_id, cfg.vector_store_name)
        store = VectorStore(
            client, store_id, chunk_size=cfg.chunk_size_tokens, chunk_overlap=cfg.chunk_overlap_tokens
        )
        files = store.list_files()
    except openai.APIError as exc:
        log.error("vector store unreachable: %s", exc)
        return 3

    # Removals are unsafe after a partial convert: an article that failed to convert
    # is still published and must not be dropped from the store.
    p = plan(docs, files, scrape_complete=(failed == 0))
    if p.removals_blocked:
        log.warning(
            "not removing any files: scraped %d article(s) (%d failed to convert) vs %d indexed; "
            "refusing to delete on a suspiciously small or incomplete scrape",
            len(articles), failed, len(files),
        )
    log.info(
        "PLAN add=%d update=%d skip=%d remove=%d duplicates=%d",
        len(p.to_add), len(p.to_update), len(p.to_skip), len(p.to_remove), len(p.duplicates),
    )

    stats = apply(p, store, cfg.dry_run, chunk_size=cfg.chunk_size_tokens, chunk_overlap=cfg.chunk_overlap_tokens)
    stats.scraped = len(articles)
    stats.vector_store_id = store_id
    stats.duration_s = time.monotonic() - started
    stats.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info(stats.summary_line())
    try:
        write_last_run(stats, LAST_RUN_PATH)
    except OSError:
        log.warning("could not write %s", LAST_RUN_PATH, exc_info=True)

    # Spec 05: a few isolated failures are tolerable, >10% means something is wrong with the store.
    attempted = stats.added + stats.updated + stats.removed + stats.failed
    if stats.failed and stats.failed > 0.1 * attempted:
        log.error("%d of %d store operations failed", stats.failed, attempted)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
