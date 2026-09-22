"""Entrypoint. Currently: scrape (spec 01) + convert to Markdown (spec 02).
Later specs add diff/upload."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from bot.config import ConfigError, load_config
from bot.logging_setup import setup_logging
from bot.markdown import slugify, write_markdown
from bot.zendesk import ScrapeError, fetch_articles

log = logging.getLogger("main")


def main() -> int:
    try:
        cfg = load_config(require_api_key=False)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    setup_logging(cfg.log_level)

    try:
        articles = fetch_articles(cfg.zendesk_base_url, cfg.zendesk_locale, cfg.max_articles)
    except ScrapeError as exc:
        log.error("%s", exc)
        return 2

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = failed = 0
    written_paths: set[Path] = set()
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
        written_paths.add(path)
        log.info("wrote %s", path)

    # Prune stale .md files left over from a previous run (e.g. renamed/unpublished
    # articles). Only do this when every article converted cleanly, so a partial
    # failure never deletes still-valid content for the articles that failed.
    if articles and failed == 0:
        for existing in out_dir.glob("*.md"):
            if existing not in written_paths:
                existing.unlink()
                log.info("removed stale %s", existing)
    elif failed:
        log.warning("skipping stale-file cleanup: %d article(s) failed to convert this run", failed)

    log.info(
        "RUN SUMMARY articles=%d written=%d failed=%d output_dir=%s",
        len(articles), written, failed, out_dir,
    )
    if articles and written == 0:
        log.error("all %d article(s) failed to convert; treating run as failed", len(articles))
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
