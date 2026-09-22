"""HTML -> Markdown converter (spec 02).

Turns an `Article` into one deterministic Markdown document:

    # {title}

    Article URL: {html_url}
    Last updated: {updated_at}
    Section: {section_name}

    ---

    {body}

Policies (for the README):
- The Zendesk API body contains only article content, so nav/ads never appear.
- Headings are demoted one level; the title owns the only `#`.
- Links are kept as written (relative stays relative, help-center article links are
  not rewritten to local .md files); `utm_*` query params are stripped.
- Video iframes become `[Video](src)`; other iframes `[Embedded content](src)`.
- Single-column tables (Zendesk's "callout box" idiom) become blockquotes. Other
  tables become GFM tables unless a cell holds block content (lists, code, nested
  tables...), in which case a bare HTML table is kept so nothing is lost.
- Long bodies (> LONG_BODY_CHARS) repeat the `Article URL:` line at the end so later
  chunks stay citable.
- Output is byte-stable for the same input (no "now" timestamps); spec 04 hashes it.
"""

from __future__ import annotations

import re
from pathlib import Path

from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

from bot.zendesk import Article

LONG_BODY_CHARS = 4000
MAX_SLUG_LEN = 120

_DROP_TAGS = ("script", "style", "noscript", "form", "button")
_KEEP_ATTRS = {"href", "src", "alt", "title", "colspan", "rowspan"}
_UNWRAP_TAGS = ("div", "figure", "section", "article", "aside", "font", "center")
_VIDEO_HOSTS = ("youtube.com", "youtube-nocookie.com", "youtu.be", "vimeo.com", "wistia", "loom.com")
_CALLOUT_RE = re.compile(r"\b(note|callout|alert|warning|tip|info|important)\b", re.I)
_LANG_RE = re.compile(r"(?:^|\s)(?:language|lang)-([A-Za-z0-9_+#-]+)")
_BLOCK_IN_CELL = ("ul", "ol", "pre", "table", "blockquote", "h2", "h3", "h4", "h5", "h6")
_ZENDESK_LEFTOVERS = ("was this article helpful",)


# --------------------------------------------------------------------------- slug


def slugify(article: Article) -> str:
    """`{id}-{title-slug}` derived from the html_url tail; lowercase [a-z0-9-]."""
    tail = urlparse(article.html_url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")
    prefix = f"{article.id}-"
    if not slug:
        slug = str(article.id)
    elif not slug.startswith(prefix) and slug != str(article.id):
        slug = prefix + slug
    if len(slug) > MAX_SLUG_LEN:
        slug = slug[:MAX_SLUG_LEN].rstrip("-")
    return slug


# ---------------------------------------------------------------------- pre-clean


def _absolute(src: str, base_url: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    return urljoin(base_url, src)


def _is_video(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in _VIDEO_HOSTS)


def _is_empty(el: Tag) -> bool:
    if el.get_text(strip=True).replace("\xa0", ""):
        return False
    return el.find(("img", "iframe", "pre", "table", "video", "hr")) is None


def _preclean(body_html: str, base_url: str) -> BeautifulSoup:
    soup = BeautifulSoup(body_html, "html.parser")

    for el in soup.find_all(_DROP_TAGS):
        el.decompose()

    for el in soup.find_all("iframe"):
        src = (el.get("src") or "").strip()
        if not src:
            el.decompose()
            continue
        src = _absolute(src, base_url)
        link = soup.new_tag("a", href=src)
        link.string = "Video" if _is_video(src) else "Embedded content"
        wrapper = soup.new_tag("p")
        wrapper.append(link)
        el.replace_with(wrapper)

    # Callouts: decide by class before attributes are stripped.
    for el in soup.find_all(("div", "p", "aside")):
        classes = " ".join(el.get("class") or [])
        if classes and _CALLOUT_RE.search(classes):
            el.name = "blockquote"

    # Zendesk UI leftovers (not present in the API body, but cheap to guard).
    for el in soup.find_all(("div", "p", "section")):
        text = el.get_text(" ", strip=True).lower()
        if text and any(text.startswith(x) for x in _ZENDESK_LEFTOVERS):
            el.decompose()

    # Demote headings so the body never uses `#`. Walk h5 -> h1 to avoid double demotion.
    for level in range(5, 0, -1):
        for el in soup.find_all(f"h{level}"):
            el.name = f"h{level + 1}"

    for el in soup.find_all(True):
        keep = set(_KEEP_ATTRS)
        if el.name in ("pre", "code"):
            keep.add("class")
        for attr in list(el.attrs):
            if attr not in keep:
                del el.attrs[attr]

    for el in soup.find_all("span"):
        el.unwrap()

    # Remove empty wrappers until stable (handles nested empties).
    changed = True
    while changed:
        changed = False
        for el in soup.find_all(("p", "div", "figure", "section", "li")):
            if el.name == "li" and el.parent is not None and el.parent.name not in ("ul", "ol"):
                continue
            if _is_empty(el):
                el.decompose()
                changed = True

    for el in soup.find_all(_UNWRAP_TAGS):
        el.unwrap()

    # Single-column tables are how Zendesk authors draw callout boxes: one blockquote per cell.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        cells = [row.find_all(("td", "th"), recursive=False) for row in rows]
        if not rows or any(len(c) != 1 for c in cells):
            continue
        for row_cells in cells:
            cell = row_cells[0]
            if _is_empty(cell):
                continue
            quote = soup.new_tag("blockquote")
            for child in list(cell.contents):
                quote.append(child)
            table.insert_before(quote)
        table.decompose()

    return soup


# --------------------------------------------------------------------- converter


def _strip_tracking(href: str) -> str:
    parts = urlparse(href)
    if not parts.query:
        return href
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not k.lower().startswith("utm_")]
    if len(kept) == len(pairs):
        return href  # untouched: never re-encode a URL we don't need to change
    return urlunparse(parts._replace(query=urlencode(kept)))


def _code_language(el: Tag) -> str:
    for node in (el, el.find("code")):
        if node is None:
            continue
        m = _LANG_RE.search(" ".join(node.get("class") or []))
        if m and m.group(1).lower() not in ("auto", "none", "plain", "plaintext", "text"):
            return m.group(1).lower()
    return ""


class _Converter(MarkdownConverter):
    class Options(MarkdownConverter.DefaultOptions):
        heading_style = "atx"
        bullets = "-"
        strip_document = "strip"
        escape_misc = False
        code_language_callback = staticmethod(_code_language)
        keep_inline_images_in = ["a", "td", "th", "li", "p", "strong", "em", "b", "i"]

    def __init__(self, base_url: str, **options):
        super().__init__(**options)
        self.base_url = base_url

    def convert_a(self, el, text, parent_tags):
        href = el.get("href")
        if href:
            el["href"] = _strip_tracking(href.strip())
        return super().convert_a(el, text, parent_tags)

    def convert_img(self, el, text, parent_tags):
        src = (el.get("src") or "").strip()
        if src:
            src = _absolute(src, self.base_url)
            el["src"] = src
        if not (el.get("alt") or "").strip():
            el["alt"] = urlparse(src).path.rstrip("/").rsplit("/", 1)[-1] or "image"
        return super().convert_img(el, text, parent_tags)

    def convert_table(self, el, text, parent_tags):
        for cell in el.find_all(("td", "th")):
            if cell.find(_BLOCK_IN_CELL) is not None or len(cell.find_all(("p", "br"))) > 1:
                return "\n\n" + str(el) + "\n\n"
        return super().convert_table(el, text, parent_tags)


# ------------------------------------------------------------------ post-process

_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^#{1,6} ")
_INNER_SPACES_RE = re.compile(r"(?<=\S) {2,}(?=\S)")


def _postprocess(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [ln.rstrip() for ln in text.split("\n")]

    out: list[str] = []
    in_fence = False
    for ln in lines:
        is_fence = bool(_FENCE_RE.match(ln))
        if not in_fence and not is_fence:
            ln = _INNER_SPACES_RE.sub(" ", ln)
        needs_gap = is_fence and not in_fence or (not in_fence and _HEADING_RE.match(ln))
        if needs_gap and out and out[-1] != "":
            out.append("")
        out.append(ln)
        if is_fence:
            in_fence = not in_fence
            if not in_fence:
                out.append("")
        elif not in_fence and _HEADING_RE.match(ln):
            out.append("")

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# --------------------------------------------------------------------- interface


def to_markdown(article: Article) -> str:
    """Deterministic Markdown document for `article` (same input -> identical bytes)."""
    soup = _preclean(article.body_html, article.html_url)
    body = _Converter(article.html_url).convert_soup(soup)

    header = (
        f"# {article.title.strip()}\n\n"
        f"Article URL: {article.html_url}\n"
        f"Last updated: {article.updated_at}\n"
        f"Section: {article.section_name or '-'}\n\n"
        "---\n\n"
    )
    doc = header + body
    if len(body) > LONG_BODY_CHARS:
        doc += f"\n\n---\n\nArticle URL: {article.html_url}\n"
    return _postprocess(doc)


def write_markdown(article: Article, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slugify(article)}.md"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(to_markdown(article))
    return path
