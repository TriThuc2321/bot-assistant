"""Entrypoint. Currently: scrape (spec 01) + convert to Markdown (spec 02).
Later specs add diff/upload."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from bot.config import ConfigError, load_config
from bot.logging_setup import setup_logging
from bot.markdown import write_markdown
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
    written = failed = 0
    for a in articles:
        try:
            path = write_markdown(a, out_dir)
        except Exception as exc:  # one bad article must not kill the run
            failed += 1
            log.warning("Failed to convert article %s (%r): %s", a.id, a.title, exc)
            continue
        written += 1
        log.debug("wrote %s", path)

    log.info(
        "RUN SUMMARY articles=%d written=%d failed=%d output_dir=%s",
        len(articles), written, failed, out_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
