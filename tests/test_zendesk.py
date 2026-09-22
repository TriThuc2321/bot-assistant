"""Scraper tests with a fake session — no network."""

from __future__ import annotations

import dataclasses

import pytest
import requests

from bot.zendesk import Article, ScrapeError, build_session, fetch_articles

BASE = "https://example.zendesk.test"
ARTICLES_URL = f"{BASE}/api/v2/help_center/en-us/articles.json"
SECTIONS_URL = f"{BASE}/api/v2/help_center/en-us/sections.json"
PAGE2_URL = f"{BASE}/api/v2/help_center/en-us/articles?page%5Bafter%5D=abc&page%5Bsize%5D=100"


def article(i: int, **overrides):
    raw = {
        "id": i,
        "title": f"Article {i}",
        "body": f"<p>Body {i}</p>",
        "html_url": f"https://example.test/hc/en-us/articles/{i}-article-{i}",
        "updated_at": "2026-01-01T00:00:00Z",
        "draft": False,
        "locale": "en-us",
        "section_id": 10,
        "label_names": ["a", "b"],
    }
    raw.update(overrides)
    return raw


def page(key, items, next_url=None):
    return {
        "meta": {"has_more": next_url is not None},
        "links": {"next": next_url},
        key: items,
    }


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


class FakeSession:
    """Routes GET calls by URL to canned responses; records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        result = self.routes[url]
        if isinstance(result, Exception):
            raise result
        return result


def sections_page():
    return page("sections", [{"id": 10, "name": "Getting Started"}, {"id": 11, "name": "Other"}])


def test_pagination_collects_all_pages_and_resolves_sections():
    session = FakeSession({
        SECTIONS_URL: FakeResponse(sections_page()),
        ARTICLES_URL: FakeResponse(page("articles", [article(1), article(2)], next_url=PAGE2_URL)),
        PAGE2_URL: FakeResponse(page("articles", [article(3, section_id=11), article(4, section_id=99)])),
    })

    result = fetch_articles(BASE, "en-us", session=session)

    assert [a.id for a in result] == [1, 2, 3, 4]
    assert result[0].section_name == "Getting Started"
    assert result[2].section_name == "Other"
    assert result[3].section_name is None and result[3].section_id == 99
    assert result[0].label_names == ["a", "b"]
    for a in result:
        assert a.title and a.body_html and a.html_url and a.updated_at
    # articles page 1 requested with cursor params and timeout
    url, params, timeout = session.calls[1]
    assert url == ARTICLES_URL
    assert params["page[size]"] == 100 and params["sort_by"] == "updated_at"
    assert timeout == (10, 30)


def test_filters_draft_locale_empty_body_and_malformed(caplog):
    session = FakeSession({
        SECTIONS_URL: FakeResponse(sections_page()),
        ARTICLES_URL: FakeResponse(page("articles", [
            article(1),
            article(2, draft=True),
            article(3, locale="fr"),
            article(4, body=""),
            article(5, body="   \n"),
            {k: v for k, v in article(6).items() if k != "id"},  # missing id
            article(7, html_url=""),
        ])),
    })

    with caplog.at_level("INFO"):
        result = fetch_articles(BASE, "en-us", session=session)

    assert [a.id for a in result] == [1]
    summary = [r.message for r in caplog.records if "Scrape complete" in r.message][0]
    assert "fetched=7 kept=1" in summary
    assert "'draft': 1" in summary and "'locale': 1" in summary
    assert "'empty_body': 2" in summary and "'malformed': 2" in summary


def test_limit_stops_before_fetching_next_page():
    session = FakeSession({
        SECTIONS_URL: FakeResponse(sections_page()),
        ARTICLES_URL: FakeResponse(page("articles", [article(1), article(2)], next_url=PAGE2_URL)),
        PAGE2_URL: FakeResponse(page("articles", [article(3)])),
    })

    result = fetch_articles(BASE, "en-us", limit=1, session=session)

    assert [a.id for a in result] == [1]
    assert PAGE2_URL not in [c[0] for c in session.calls]


def test_limit_zero_means_all():
    session = FakeSession({
        SECTIONS_URL: FakeResponse(sections_page()),
        ARTICLES_URL: FakeResponse(page("articles", [article(1), article(2)])),
    })
    assert len(fetch_articles(BASE, "en-us", limit=0, session=session)) == 2


def test_article_list_connection_error_is_fatal():
    session = FakeSession({
        SECTIONS_URL: FakeResponse(sections_page()),
        ARTICLES_URL: requests.ConnectionError("boom"),
    })
    with pytest.raises(ScrapeError):
        fetch_articles(BASE, "en-us", session=session)


def test_article_list_http_500_is_fatal():
    session = FakeSession({
        SECTIONS_URL: FakeResponse(sections_page()),
        ARTICLES_URL: FakeResponse(status=500),
    })
    with pytest.raises(ScrapeError):
        fetch_articles(BASE, "en-us", session=session)


def test_sections_failure_is_not_fatal():
    session = FakeSession({
        SECTIONS_URL: requests.ConnectionError("no sections"),
        ARTICLES_URL: FakeResponse(page("articles", [article(1)])),
    })
    result = fetch_articles(BASE, "en-us", session=session)
    assert len(result) == 1 and result[0].section_name is None


def test_session_retries_on_429_and_5xx_with_retry_after():
    session = build_session()
    retry = session.get_adapter("https://x").max_retries
    assert retry.total >= 3
    assert {429, 500, 502, 503, 504} <= set(retry.status_forcelist)
    assert retry.respect_retry_after_header is True
    assert retry.backoff_factor > 0
    assert "optibot" in session.headers["User-Agent"]


def test_article_is_frozen():
    a = Article(1, "t", "<p>b</p>", "https://u", "2026-01-01T00:00:00Z", None, None, [])
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.title = "x"
