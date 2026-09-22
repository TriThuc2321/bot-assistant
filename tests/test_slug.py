"""Slug tests (spec 02): derived from html_url, lowercase [a-z0-9-], unique and stable."""

from __future__ import annotations

import re

from bot.markdown import MAX_SLUG_LEN, slugify
from bot.zendesk import Article


def article(id: int, url: str) -> Article:
    return Article(id, "Title", "<p>b</p>", url, "2026-01-01T00:00:00Z", None, None, [])


def test_zendesk_url_tail():
    a = article(360012345678, "https://x.test/hc/en-us/articles/360012345678-How-to-add-YouTube")
    assert slugify(a) == "360012345678-how-to-add-youtube"


def test_special_chars_and_unicode_collapse_to_dashes():
    a = article(5, "https://x.test/hc/en-us/articles/5-Caf%C3%A9_%26_Bar--(v2)!")
    s = slugify(a)
    assert re.fullmatch(r"[a-z0-9-]+", s) and "--" not in s
    assert s.startswith("5-") and not s.endswith("-")


def test_trailing_slash_and_query_ignored():
    a = article(7, "https://x.test/hc/en-us/articles/7-Hello-World/?foo=bar#x")
    assert slugify(a) == "7-hello-world"


def test_id_prefixed_when_url_tail_lacks_it():
    assert slugify(article(9, "https://x.test/hc/en-us/articles/Hello")) == "9-hello"
    assert slugify(article(9, "https://x.test/")) == "9"
    assert slugify(article(9, "https://x.test/hc/en-us/articles/9")) == "9"


def test_same_title_different_ids_are_unique():
    a = article(1, "https://x.test/hc/en-us/articles/1-Same-Title")
    b = article(2, "https://x.test/hc/en-us/articles/2-Same-Title")
    assert slugify(a) != slugify(b)


def test_slug_is_stable_and_capped():
    a = article(3, "https://x.test/hc/en-us/articles/3-" + "word-" * 100)
    s = slugify(a)
    assert s == slugify(a)
    assert len(s) <= MAX_SLUG_LEN and s.startswith("3-") and not s.endswith("-")
