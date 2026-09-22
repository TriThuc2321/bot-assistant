# Spec 01 — Zendesk Scraper (`bot/zendesk.py`)

## Purpose
Fetch all published articles from support.optisigns.com as structured data. No HTML page scraping — use the public Help Center API.

## Endpoints
- Articles: `GET {base}/api/v2/help_center/{locale}/articles.json?per_page=100&sort_by=updated_at&sort_order=desc`
- Sections (optional, for metadata): `GET {base}/api/v2/help_center/{locale}/sections.json?per_page=100`
- Categories (optional): `GET {base}/api/v2/help_center/{locale}/categories.json?per_page=100`

No auth needed for public articles. Verify the exact response shape against one live call before coding.

## Pagination
- Follow `next_page` until it is `null` (offset pagination), **or** use cursor pagination (`page[size]=100`, follow `links.next` while `meta.has_more`). Pick one; cursor is preferred by Zendesk for large sets.
- Respect `MAX_ARTICLES` if > 0.

## Output model
```python
@dataclass(frozen=True)
class Article:
    id: int
    title: str
    body_html: str          # may be None/empty → skip with warning
    html_url: str           # canonical public URL, used for citations
    updated_at: str         # ISO-8601 from Zendesk
    section_id: int | None
    section_name: str | None   # resolved if sections fetched
    label_names: list[str]
```

## Filtering
- Skip `draft == True`.
- Skip articles with empty `body`.
- Skip non-`en-us` locale (unless configured otherwise).
- Log count of skipped-by-filter with reason.

## Reliability
- `requests.Session` with timeout (10s connect / 30s read).
- Retry on 429 / 5xx with exponential backoff (3–5 attempts). On 429, honor `Retry-After`.
- Set a `User-Agent` identifying the job.
- A failed fetch of the article list is fatal (exit non-zero). A single malformed article is logged and skipped.

## Interface
```python
def fetch_articles(base_url: str, locale: str, limit: int = 0) -> list[Article]
```

## Acceptance criteria
- [ ] Returns ≥ 30 articles (expect the full catalog, likely hundreds).
- [ ] Every returned `Article` has non-empty `title`, `body_html`, `html_url`.
- [ ] Handles pagination; total matches the API's reported `count`, minus filtered.
- [ ] Unit-testable with a mocked session (spec 07).
