"""Entrypoint. Currently: scrape only (spec 01). Later specs add convert/diff/upload."""

from __future__ import annotations

import logging
import sys

from bot.config import ConfigError, load_config
from bot.logging_setup import setup_logging
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

    for a in articles[:5]:
        log.info("  %s  [%s]  %s", a.id, a.section_name or "-", a.html_url)
    log.info("RUN SUMMARY articles=%d", len(articles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
