# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OptiBot Mini-Clone: a single Python job that scrapes OptiSigns' Zendesk Help Center,
converts each article to deterministic Markdown, and uploads them to an OpenAI Vector Store
for a support-bot assistant (delta detection of new/changed articles is spec 04, not yet built).
The full design lives in `specs/00-overview.md` through `specs/07-assistant-tests-readme.md`
— read the relevant spec before implementing a piece of it; each spec maps to one module.

Build order (also the intended commit order): scraper → converter → uploader → delta →
docker → deploy → tests → readme. Specs 00–03 (`bot/zendesk.py`, `bot/markdown.py`,
`bot/vector_store.py`) are implemented so far; `bot/sync.py` (spec 04) does not exist yet.

## Commands

```bash
pip install -r requirements.txt
python main.py                 # scrape + convert + upload anything not yet in the store
python main.py --scrape-only   # write .md files only; no API key needed
pytest                         # run all tests, no network required
pytest tests/test_markdown.py  # run a single test file
pytest tests/test_markdown.py::test_name -v   # run a single test
```

No lint/format command is configured in this repo.

## Architecture

- `bot/config.py` — loads `.env` via `python-dotenv`, validates env vars into a frozen
  `Config` dataclass. Accepts both `OPENAI_API_KEY` and `API_KEY`. Enforces
  `CHUNK_OVERLAP_TOKENS <= CHUNK_SIZE_TOKENS // 2`.
- `bot/zendesk.py` — scrapes the public Zendesk Help Center JSON API (no auth). Cursor
  pagination via `links.next` / `meta.has_more`. Skips draft/wrong-locale/empty-body/malformed
  articles individually rather than failing the whole run; only a failure to fetch the article
  list itself raises `ScrapeError`. Retries 429/5xx with backoff via `urllib3.util.retry.Retry`.
- `bot/markdown.py` — converts an `Article` to one deterministic Markdown document (same
  input → identical bytes; spec 04's hash-based delta detection depends on this). Key
  behaviors documented in the module docstring: headings demoted one level (title owns the
  only `#`), `utm_*` query params stripped from links, video iframes → `[Video](src)`,
  single-column tables → blockquotes (Zendesk's callout idiom), tables with block content in
  a cell fall back to raw HTML instead of lossy GFM conversion. `slugify()` derives
  `{id}-{title-slug}` from the article's `html_url` tail.
- `bot/vector_store.py` — OpenAI Files + Vector Stores API only (no `client.beta.assistants`).
  `resolve_vector_store()` uses `VECTOR_STORE_ID` if set, else find-or-create by name.
  `VectorStore.add()` uploads with the `slug.md` filename, attaches with an explicit static
  chunking strategy and per-file attributes (`article_id`, `slug`, `content_hash`,
  `source_updated_at`, `url` — these are spec 04's sync state), polls to `completed`, and on
  failure deletes both the vs-file and the File before raising `UploadError` (no orphans).
  `remove()` tolerates 404s so it's retry-safe; `replace()` uploads before deleting.
  `estimate_chunks()` is a local tiktoken estimate — the API never reports chunk counts.
  Tests stub `count_tokens` so tiktoken never downloads its encoding.
- `main.py` — orchestrates scrape → convert → write `.md` files, prunes stale `.md` files
  from previous runs (only when every article converted cleanly, so a partial failure never
  deletes still-good content), then uploads every article whose `article_id` isn't already
  indexed (spec 04 replaces this with hash-based add/update/skip/remove). `DRY_RUN` still
  resolves/lists the store but writes nothing. Exit codes: `0` ok, `1` config error,
  `2` scrape failed (can't list articles, or none converted), `3` vector store unreachable.
- `articles/` — generated Markdown output, one file per article, named `{slug}.md`.

## Working in this repo

- Don't add comments unless they explain a non-obvious *why*. This codebase already follows
  that convention closely — match it.
- `bot/markdown.py` output must stay byte-stable for identical input; be deliberate about any
  change there since spec 04 hashes the output for delta detection.
- Tests run with no network (mocked sessions for `bot/zendesk.py`).
