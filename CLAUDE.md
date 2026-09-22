# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OptiBot Mini-Clone: a single Python job that scrapes OptiSigns' Zendesk Help Center,
converts each article to deterministic Markdown, and uploads them to an OpenAI Vector Store
for a support-bot assistant, uploading only new/changed articles (hash-based delta sync).
The full design lives in `specs/00-overview.md` through `specs/07-assistant-tests-readme.md`
— read the relevant spec before implementing a piece of it; each spec maps to one module.

Build order (also the intended commit order): scraper → converter → uploader → delta →
docker → deploy → tests → readme. All specs (00–07) are implemented. The OptiBot assistant
itself lives in the OpenAI Playground (spec 07); `scripts/ask.py` is the API fallback.

## Commands

```bash
pip install -r requirements-dev.txt   # runtime deps (requirements.txt) + pytest
python main.py                 # scrape + convert + delta sync into the vector store
python main.py --dry-run       # (or DRY_RUN=true) print the add/update/remove plan; lists the store, writes nothing
python main.py --limit 5       # (or MAX_ARTICLES=5) first N articles only; never removes files from the store
python main.py --scrape-only   # write .md files only; no API key needed
docker build -t kb-sync .
docker run --rm -e API_KEY=sk-... kb-sync    # runs once, logs RUN SUMMARY, exits 0
python -m scripts.ask "How do I add a YouTube video?"   # ask OptiBot via Responses API + file_search
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
  `source_updated_at`, `url` — these are the sync state), polls to `completed`, and on
  failure deletes both the vs-file and the File before raising `UploadError` (no orphans).
  `remove()` tolerates 404s so it's retry-safe; `replace()` uploads before deleting.
  `list_files()` returns the raw listing (foreign files included, `article_id=""`);
  deduplication lives in `bot/sync.plan()`. `estimate_chunks()` is a local tiktoken
  estimate — the API never reports chunk counts. Tests stub `count_tokens` so tiktoken
  never downloads its encoding.
- `bot/sync.py` — delta detection. `plan(docs, files, scrape_complete=)` is pure: newest
  file per `article_id` wins (older copies → `duplicates`, always deleted), files without
  `article_id` are ignored, then add / update (hash differs **or** indexed status isn't
  `completed`) / skip / remove. Removals are suppressed (`removals_blocked`) when the scrape
  is empty, incomplete, or < 50% of the indexed count, so an API hiccup can't wipe the store.
  `apply()` runs adds → updates (`replace`, upload-then-delete) → duplicates → removals,
  counting per-file failures instead of aborting; `dry_run` logs `DRY RUN would …` and
  makes no store calls. Returns `RunStats` (`summary_line()`, `write_last_run()` →
  `artifacts/last_run.json`).
- `main.py` — orchestrates scrape → convert → write `.md` files, prunes stale `.md` files
  from previous runs (only when every article converted cleanly, so a partial failure never
  deletes still-good content), then `plan()` + `apply()` against the store (a `--limit` /
  `MAX_ARTICLES` run passes `scrape_complete=False`, so it never removes files), logs one
  `RUN SUMMARY` line and writes `artifacts/last_run.json`. `DRY_RUN` still resolves/lists
  the store but writes nothing. Exit codes: `0` ok, `1` config error, `2` scrape failed
  (can't list articles, or none converted), `3` vector store unreachable or > 10% of store
  operations failed.
- `Dockerfile` — `python:3.12-slim`, non-root user, pre-downloads tiktoken's `o200k_base`
  at build time (`TIKTOKEN_CACHE_DIR=/app/.tiktoken`) so runs don't fetch it. Only
  `requirements.txt` goes into the image; `pytest` lives in `requirements-dev.txt`.
- `.github/workflows/daily.yml` — spec 06: cron `0 2 * * *` + `workflow_dispatch`, builds
  the image and runs it with `OPENAI_API_KEY` / `VECTOR_STORE_ID` repo secrets, uploads
  `artifacts/` (`run.log`, `last_run.json`) as the `last-run` artifact and writes the
  `RUN SUMMARY` line to the job summary. Gotchas it handles: logs go to stderr (`2>&1`),
  `shell: bash` for pipefail so `tee` doesn't mask failures, and a world-writable
  `artifacts/` because the container's `app` uid differs from the runner's. Schedules only
  fire from `main`.
- `scripts/ask.py` — spec 07 fallback for the Playground assistant: `SYSTEM_PROMPT` must stay
  byte-identical to the brief (`tests/test_ask.py` enforces it); calls `client.responses.create`
  with a `file_search` tool on the resolved store and prints the answer + cited filenames.
  Run with `python -m` so `bot` is importable.
- `.github/workflows/tests.yml` — `pytest` on every push and on PRs to `main`.
- `docs/samples/` — a few committed converted articles for reviewers (kept out of `articles/`,
  which `main.py` prunes). `docs/screenshots/` holds the Playground screenshots the README embeds.
- `articles/` — generated Markdown output, one file per article, named `{slug}.md`.

## Working in this repo

- Don't add comments unless they explain a non-obvious *why*. This codebase already follows
  that convention closely — match it.
- `bot/markdown.py` output must stay byte-stable for identical input; be deliberate about any
  change there since spec 04 hashes the output for delta detection.
- Tests run with no network (mocked sessions for `bot/zendesk.py`).
