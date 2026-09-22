# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OptiBot Mini-Clone: a single Python job that scrapes OptiSigns' Zendesk Help Center,
converts each article to deterministic Markdown, and (in later specs, not yet built)
uploads new/changed articles to an OpenAI Vector Store for a support-bot assistant.
The full design lives in `specs/00-overview.md` through `specs/07-assistant-tests-readme.md`
— read the relevant spec before implementing a piece of it; each spec maps to one module.

Build order (also the intended commit order): scraper → converter → uploader → delta →
docker → deploy → tests → readme. Only specs 00–02 (`bot/zendesk.py`, `bot/markdown.py`)
are implemented so far; `bot/vector_store.py` and `bot/sync.py` (specs 03–04) do not exist yet.

## Commands

```bash
pip install -r requirements.txt
python main.py                 # scrape + convert only for now (no uploader yet)
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
- `main.py` — orchestrates scrape → convert → write `.md` files, then prunes stale `.md`
  files from previous runs — but only when every article in the run converted cleanly, so a
  partial failure never deletes still-good content. Exit codes: `0` ok, `1` config error,
  `2` scrape failed, `3` all articles failed to convert. (Later specs add vector-store
  upload/delta/prune logic on top of this, per `specs/05-main-and-docker.md`.)
- `articles/` — generated Markdown output, one file per article, named `{slug}.md`.

## Working in this repo

- Don't add comments unless they explain a non-obvious *why*. This codebase already follows
  that convention closely — match it.
- `bot/markdown.py` output must stay byte-stable for identical input; be deliberate about any
  change there since spec 04 hashes the output for delta detection.
- Tests run with no network (mocked sessions for `bot/zendesk.py`).
