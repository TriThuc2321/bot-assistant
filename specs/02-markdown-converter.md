# Spec 02 — HTML → Markdown Converter (`bot/markdown.py`)

Worth **25 points** (scrape & clean quality). This is where to spend care.

## Purpose
Turn an `Article` into one clean, self-describing Markdown file.

## Output file
- Path: `{OUTPUT_DIR}/{slug}.md`
- Slug: from `html_url` path tail (Zendesk format is `.../articles/{id}-{Title-Slug}`) → lowercase, keep `[a-z0-9-]`. Using `{id}-{title-slug}` guarantees uniqueness and stability.
- UTF-8, `\n` line endings, single trailing newline.

## Document structure
```markdown
# {title}

Article URL: {html_url}
Last updated: {updated_at}
Section: {section_name}

---

{converted body}
```
**Why the `Article URL:` line matters:** the system prompt tells the bot to cite `Article URL:` lines. If the line isn't inside the chunk text, the bot cannot cite it. Keep it at the top so it lands in the first chunk; consider repeating the URL at the end of long articles (optional, note in README).

## Pre-clean (BeautifulSoup, before conversion)
Remove:
- `<script>`, `<style>`, `<iframe>` (replace video iframes with a link: `[Video](src)`), `<noscript>`, `<form>`, `<button>`
- Empty `<p>`, `<div>`, `<span>` wrappers; `&nbsp;` → space
- Inline `style`/`class` attributes (irrelevant to MD but helps markdownify)
- Zendesk UI leftovers if present (e.g., "Was this article helpful?", related-articles blocks) — the API body usually doesn't include them; verify on samples.

Nav/ads: the API body contains only article content, so "remove nav/ads" is satisfied by design. State this in the README.

## Conversion rules (`markdownify`, `heading_style="ATX"`)
| Element | Rule |
|---|---|
| Headings | `#`…`######`; demote so the body never uses `#` (title owns H1) |
| Code | `<pre><code>` → fenced ``` block; keep language from `class="language-x"` if present; `<code>` inline → backticks |
| Lists | Nested lists preserved; `-` bullets |
| Tables | Convert to GFM tables; if cells contain block content, fall back to keeping simple HTML table |
| Images | `![alt](absolute_src)`; alt falls back to filename |
| Links | See below |
| Callouts/notes (`div.note`, etc.) | `> ` blockquote |
| Bold/italic | Preserved |

## Links
- Keep relative links relative ("preserve relative links" in brief). Don't rewrite them to absolute.
- Links to other help-center articles (`/hc/en-us/articles/{id}-...`): keep as-is; optionally add a mapping to local `{slug}.md` in a helper. Pick one policy and document it.
- Strip tracking query params (`utm_*`).
- Anchors (`#section`) kept.

## Post-process
- Collapse 3+ blank lines → 2.
- Strip trailing whitespace per line.
- Ensure blank line before/after headings, lists, code fences.

## Interface
```python
def slugify(article: Article) -> str
def to_markdown(article: Article) -> str
def write_markdown(article: Article, out_dir: Path) -> Path
```
`to_markdown` must be **deterministic**: same input → byte-identical output (required for hashing in spec 04). No timestamps of "now" in the document.

## Acceptance criteria
- [ ] ≥ 30 `.md` files written; filenames unique and stable across runs.
- [ ] Spot-check 5 articles (one with a table, one with code, one with images, one with video, one long) — render cleanly in a Markdown previewer.
- [ ] No raw `<div>`/`<span>`/`style=` left in output.
- [ ] Each file starts with `# title` and contains `Article URL:`.
- [ ] Converting the same article twice gives identical bytes.
