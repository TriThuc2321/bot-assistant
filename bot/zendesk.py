"""Zendesk Help Center scraper (spec 01).

Uses the public Help Center JSON API with cursor pagination. No auth needed
for public articles. Verified response shape (2026-09-22):
  articles page: {"meta": {"has_more", "after_cursor"}, "links": {"next"}, "articles": [...]}
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "optibot-kb-sync/0.1 (+https://github.com; support-kb-sync job)"
TIMEOUT = (10, 30)  # connect, read (seconds)
PAGE_SIZE = 100


class ScrapeError(Exception):
    """Fatal: the article list could not be fetched."""


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    body_html: str
    html_url: str
    updated_at: str
    section_id: Optional[int]
    section_name: Optional[str]
    label_names: list[str]


def build_session() -> requests.Session:
    """Session with UA header and retry on 429/5xx (honors Retry-After)."""
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept"] = "application/json"
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _get_json(session: requests.Session, url: str, params: Optional[dict] = None) -> dict:
    resp = session.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _paginate(session: requests.Session, url: str, params: dict) -> Iterator[dict]:
    """Yield each page dict, following cursor `links.next` while `meta.has_more`."""
    page = _get_json(session, url, params)
    yield page
    while page.get("meta", {}).get("has_more") and page.get("links", {}).get("next"):
        page = _get_json(session, page["links"]["next"])
        yield page


def fetch_sections(session: requests.Session, base_url: str, locale: str) -> dict[int, str]:
    """Map section id -> name. Non-fatal: returns {} on any failure."""
    url = f"{base_url}/api/v2/help_center/{locale}/sections.json"
    sections: dict[int, str] = {}
    try:
        for page in _paginate(session, url, {"page[size]": PAGE_SIZE}):
            for s in page.get("sections", []):
                sections[int(s["id"])] = str(s.get("name") or "")
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("Could not fetch sections (%s); section names will be empty", exc)
        return {}
    log.info("Fetched %d sections", len(sections))
    return sections


def _parse_article(raw: dict[str, Any], sections: dict[int, str]) -> Article:
    section_id = raw.get("section_id")
    section_id = int(section_id) if section_id is not None else None
    labels = raw.get("label_names") or []
    return Article(
        id=int(raw["id"]),
        title=str(raw["title"]).strip(),
        body_html=str(raw["body"]),
        html_url=str(raw["html_url"]),
        updated_at=str(raw["updated_at"]),
        section_id=section_id,
        section_name=sections.get(section_id) if section_id is not None else None,
        label_names=[str(x) for x in labels],
    )


def fetch_articles(
    base_url: str,
    locale: str,
    limit: int = 0,
    session: Optional[requests.Session] = None,
) -> list[Article]:
    """Fetch all published articles. Raises ScrapeError if the list can't be fetched."""
    base_url = base_url.rstrip("/")
    session = session or build_session()
    sections = fetch_sections(session, base_url, locale)

    url = f"{base_url}/api/v2/help_center/{locale}/articles.json"
    params = {"page[size]": PAGE_SIZE, "sort_by": "updated_at", "sort_order": "desc"}

    kept: list[Article] = []
    skipped: Counter[str] = Counter()
    fetched = 0
    done = False

    try:
        for page in _paginate(session, url, params):
            for raw in page.get("articles", []):
                fetched += 1
                if raw.get("draft"):
                    skipped["draft"] += 1
                    continue
                if raw.get("locale") and raw["locale"] != locale:
                    skipped["locale"] += 1
                    continue
                if not (raw.get("body") or "").strip():
                    skipped["empty_body"] += 1
                    log.warning("Skipping article %s (%r): empty body", raw.get("id"), raw.get("title"))
                    continue
                try:
                    article = _parse_article(raw, sections)
                except (KeyError, TypeError, ValueError) as exc:
                    skipped["malformed"] += 1
                    log.warning("Skipping malformed article %s: %s", raw.get("id"), exc)
                    continue
                if not (article.title and article.html_url):
                    skipped["malformed"] += 1
                    log.warning("Skipping article %s: missing title/html_url", article.id)
                    continue
                kept.append(article)
                if limit > 0 and len(kept) >= limit:
                    done = True
                    break
            if done:
                break
    except (requests.RequestException, ValueError) as exc:
        raise ScrapeError(f"Failed to fetch article list from {url}: {exc}") from exc

    log.info(
        "Scrape complete: fetched=%d kept=%d skipped=%s%s",
        fetched, len(kept), dict(skipped), f" (limit={limit})" if limit > 0 else "",
    )
    return kept
