# Spec 00 — Overview & Architecture

Part 1 of the take-home: **OptiBot Mini-Clone**. Budget: ~8 focused hours.

## Goal
A single Python job that:
1. Pulls every article from support.optisigns.com (Zendesk Help Center API),
2. Converts each to clean Markdown (`<slug>.md`),
3. Uploads only new/changed articles to an OpenAI Vector Store,
4. Runs once per day in Docker on a public scheduler, logging `added / updated / skipped`.

An assistant (OpenAI Playground) answers questions over that vector store using the verbatim system prompt.

## Decisions (defaults — change if you have a reason)
| Decision | Choice | Why |
|---|---|---|
| AI platform | OpenAI (Responses API + Vector Stores + `file_search`) | Vector store API has explicit chunking control + per-file attributes. The Assistants API was **sunset on 2026-08-26** — do not use `client.beta.assistants` / `threads`. |
| Language | Python 3.12 | Brief says "via Python script". |
| Source | Zendesk Help Center API (JSON) | Hint in brief; returns clean article HTML without nav/ads, plus `updated_at` and `html_url`. |
| HTML → MD | `markdownify` + BeautifulSoup pre-clean | Controllable, supports code blocks/tables/headings. |
| State for delta | Vector-store file **attributes** (article_id, content hash) | No extra infra; state lives next to the data. See spec 04. |
| Scheduler | GitHub Actions `schedule` (primary) | Free, public log URL, runs the Docker image. Alternative: DigitalOcean App Platform Job / Railway cron. |

## Repo layout
```
<cryptic-repo-name>/          # must NOT contain "optisigns"
├── main.py                   # entrypoint: scrape → convert → diff → upload → log → exit 0
├── bot/
│   ├── config.py             # env loading/validation
│   ├── zendesk.py            # spec 01
│   ├── markdown.py           # spec 02
│   ├── vector_store.py       # spec 03
│   ├── sync.py               # spec 04 (delta logic)
│   └── logging_setup.py
├── scripts/ask.py            # optional CLI sanity check via Responses API
├── tests/                    # spec 07
├── articles/                 # generated .md (gitignored, or commit a sample)
├── Dockerfile
├── .github/workflows/daily.yml
├── .env.sample
├── requirements.txt
└── README.md
```

## Data flow
```
Zendesk API ──► Article(id, title, html, html_url, updated_at, section)
            ──► Markdown doc (front-matter header + body)  ──► articles/<slug>.md
            ──► sha256(md) ──► compare with vector-store attributes
            ──► ADD / UPDATE (delete old + upload new) / SKIP
            ──► run summary log + artifacts/last_run.json
```

## Configuration (env)
| Var | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | Brief shows `-e API_KEY=...`; accept `API_KEY` as an alias. |
| `VECTOR_STORE_ID` | no | — | If empty, find-or-create by name. |
| `VECTOR_STORE_NAME` | no | `optibot-kb` | |
| `ZENDESK_BASE_URL` | no | `https://support.optisigns.com` | |
| `ZENDESK_LOCALE` | no | `en-us` | |
| `MAX_ARTICLES` | no | `0` (= all) | Handy for local testing. |
| `CHUNK_SIZE_TOKENS` | no | `800` | See spec 03. |
| `CHUNK_OVERLAP_TOKENS` | no | `200` | ≤ half of chunk size (API rule). |
| `OUTPUT_DIR` | no | `articles` | |
| `DRY_RUN` | no | `false` | Compute delta, don't upload. |
| `LOG_LEVEL` | no | `INFO` | |

## Spec index
- 01 — Zendesk scraper
- 02 — HTML → Markdown converter
- 03 — Vector store uploader & chunking
- 04 — Delta detection & state
- 05 — `main.py` orchestration & Dockerfile
- 06 — Daily deployment & logs
- 07 — Assistant setup, tests, README & deliverables checklist