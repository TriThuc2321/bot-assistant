"""HTML -> Markdown conversion tests (spec 02). Pure functions, no network."""

from __future__ import annotations

from bot.markdown import LONG_BODY_CHARS, to_markdown, write_markdown
from bot.zendesk import Article

URL = "https://support.test/hc/en-us/articles/123-my-title"


def make_article(body: str, **overrides) -> Article:
    fields = dict(
        id=123,
        title="My Title",
        body_html=body,
        html_url=URL,
        updated_at="2026-01-01T00:00:00Z",
        section_id=1,
        section_name="Getting Started",
        label_names=[],
    )
    fields.update(overrides)
    return Article(**fields)


FIXTURE_HTML = """
<h1>Intro</h1>
<p style="color:red" class="wysiwyg">Hello <strong>world</strong>&nbsp;and <em>friends</em> <span>span text</span></p>
<div class="note">Heads up!</div>
<script>alert(1)</script><style>p{}</style><noscript>ns</noscript>
<form><input><button>Submit</button></form>
<div><div></div><p></p><span></span></div>
<pre class="wysiwyg-code-block"><code class="language-python">print("hi")</code></pre>
<pre><code class="language-auto">ls -la</code></pre>
<p>Inline <code>code</code> here.</p>
<ol><li>One<ul><li>Nested</li></ul></li><li>Two</li></ol>
<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>
<table><tr><td><ul><li>x</li></ul></td><td>y</td></tr></table>
<p><img src="/hc/article_attachments/999" alt=""></p>
<img src="https://cdn.test/pic.png" alt="Pic">
<iframe src="//www.youtube-nocookie.com/embed/abc" allowfullscreen></iframe>
<iframe src="https://www.canva.com/design/x/view?embed"></iframe>
<p><a href="/hc/en-us/articles/2-other#sec">rel</a>
<a href="https://a.test/p?utm_source=g&amp;utm_medium=cpc&amp;x=1#top">utm</a></p>
<h2>Sub</h2><hr><p>End</p>
"""

EXPECTED = """# My Title

Article URL: https://support.test/hc/en-us/articles/123-my-title
Last updated: 2026-01-01T00:00:00Z
Section: Getting Started

---

## Intro

Hello **world** and *friends* span text

> Heads up!

```python
print("hi")
```

```
ls -la
```

Inline `code` here.

1. One
   - Nested
2. Two

| A | B |
| --- | --- |
| 1 | 2 |

<table><tr><td><ul><li>x</li></ul></td><td>y</td></tr></table>

![999](https://support.test/hc/article_attachments/999)

![Pic](https://cdn.test/pic.png)

[Video](https://www.youtube-nocookie.com/embed/abc)

[Embedded content](https://www.canva.com/design/x/view?embed)

[rel](/hc/en-us/articles/2-other#sec)
[utm](https://a.test/p?x=1#top)

### Sub

---

End
"""


def test_fixture_matches_snapshot():
    assert to_markdown(make_article(FIXTURE_HTML)) == EXPECTED


def test_deterministic_and_well_formed():
    a = make_article(FIXTURE_HTML)
    md1, md2 = to_markdown(a), to_markdown(a)
    assert md1.encode("utf-8") == md2.encode("utf-8")
    assert md1.startswith("# My Title\n")
    assert f"Article URL: {URL}" in md1
    assert md1.endswith("\n") and not md1.endswith("\n\n")
    assert "\n\n\n" not in md1
    assert "\r" not in md1
    for raw in ("<div", "<span", "style=", "class=", "<script", "<iframe", "\xa0"):
        assert raw not in md1


def test_body_never_uses_h1():
    md = to_markdown(make_article("<h1>A</h1><h2>B</h2><h6>C</h6>"))
    body = md.split("---\n", 1)[1]
    assert "## A" in body and "### B" in body and "###### C" in body
    assert not any(line.startswith("# ") for line in body.splitlines())


def test_missing_section_and_blank_title_are_handled():
    md = to_markdown(make_article("<p>x</p>", section_name=None, title="  Spaced  "))
    assert md.startswith("# Spaced\n") and "Section: -\n" in md


def test_long_article_repeats_url_at_end():
    body = "".join(f"<p>Paragraph {i} with some filler text.</p>" for i in range(400))
    md = to_markdown(make_article(body))
    assert len(md) > LONG_BODY_CHARS
    assert md.count(f"Article URL: {URL}") == 2
    assert md.rstrip().endswith(f"Article URL: {URL}")

    short = to_markdown(make_article("<p>short</p>"))
    assert short.count("Article URL:") == 1


def test_callout_variants_become_blockquotes():
    md = to_markdown(make_article('<div class="alert alert-warning"><p>Careful</p></div>'))
    assert "> Careful" in md


def test_single_column_table_becomes_blockquotes():
    html = "<table><tbody><tr><td><p>Note one</p><ul><li>a</li></ul></td></tr><tr><td></td></tr>" \
           "<tr><td><p>Note two</p></td></tr></tbody></table>"
    md = to_markdown(make_article(html))
    assert "<table" not in md
    assert "> Note one" in md and "> - a" in md and "> Note two" in md


def test_utm_only_query_is_removed_entirely():
    md = to_markdown(make_article('<a href="https://a.test/p?utm_source=x">l</a>'))
    assert "[l](https://a.test/p)" in md


def test_write_markdown_creates_slug_file(tmp_path):
    a = make_article("<p>hi</p>")
    path = write_markdown(a, tmp_path / "out")
    assert path == tmp_path / "out" / "123-my-title.md"
    data = path.read_bytes()
    assert data == to_markdown(a).encode("utf-8")
    assert b"\r\n" not in data
    # second write is byte-identical (stable across runs)
    assert write_markdown(a, tmp_path / "out").read_bytes() == data
