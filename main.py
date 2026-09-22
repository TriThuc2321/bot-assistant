"""Entrypoint: scrape (spec 01) -> convert to Markdown (spec 02) -> upload (spec 03).

Upload is currently "everything not yet indexed"; spec 04 replaces that loop with
hash-based add/update/skip/remove."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import openai

from bot.config import ConfigError, load_config
from bot.logging_setup import setup_logging
from bot.markdown import slugify, write_markdown
from bot.vector_store import (
    UploadError,
    VectorStore,
    build_client,
    content_hash,
    estimate_chunks,
    resolve_vector_store,
)
from bot.zendesk import Article, ScrapeError, fetch_articles

log = logging.getLogger("main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync the help center into an OpenAI vector store.")
    parser.add_argument("--scrape-only", action="store_true", help="write .md files, skip the vector store")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
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
    docs: list[tuple[Article, Path]] = []
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
        docs.append((a, path))
        log.info("wrote %s", path)

    # Prune stale .md files left over from a previous run (e.g. renamed/unpublished
    # articles). Only do this when every article converted cleanly, so a partial
    # failure never deletes still-valid content for the articles that failed.
    written_paths = {path for _, path in docs}
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

    try:
        client = build_client(cfg.openai_api_key)
        store_id = resolve_vector_store(client, cfg.vector_store_id, cfg.vector_store_name)
        store = VectorStore(
            client, store_id, chunk_size=cfg.chunk_size_tokens, chunk_overlap=cfg.chunk_overlap_tokens
        )
        index = store.list_indexed()
    except openai.APIError as exc:
        log.error("vector store unreachable: %s", exc)
        return 3

    embedded = skipped = upload_failed = chunks = 0
    for a, path in docs:
        if str(a.id) in index:
            skipped += 1
            continue
        text = path.read_text(encoding="utf-8")
        attrs = {
            "article_id": a.id,
            "slug": path.stem,
            "content_hash": content_hash(text),
            "source_updated_at": a.updated_at,
            "url": a.html_url,
        }
        n_chunks = estimate_chunks(text, cfg.chunk_size_tokens, cfg.chunk_overlap_tokens)
        if cfg.dry_run:
            log.info("DRY RUN would upload %s (chunks≈%d)", path.name, n_chunks)
            continue
        try:
            store.add(path, attrs)
        except UploadError as exc:
            upload_failed += 1
            log.warning("upload failed: %s", exc)
            continue
        embedded += 1
        chunks += n_chunks

    log.info(
        "RUN SUMMARY articles=%d written=%d failed=%d files_embedded=%d chunks_estimated=%d "
        "skipped=%d upload_failed=%d dry_run=%s vector_store=%s",
        len(articles), written, failed, embedded, chunks, skipped, upload_failed, cfg.dry_run, store_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
