# Spec 05 — `main.py` Orchestration & Dockerfile

## main.py flow
```
1. load + validate config (fail fast if no API key)
2. articles = fetch_articles(...)
3. docs = [to_markdown + write file + hash] for each
4. store = resolve vector store
5. index = store.list_indexed()
6. p = plan(docs, index); stats = apply(p, store, dry_run)
7. log RUN SUMMARY; write artifacts/last_run.json
8. exit code
```

## Exit codes
| Code | When |
|---|---|
| 0 | Run completed (including runs where some single files failed but ≤ threshold) |
| 1 | Config error (missing key) |
| 2 | Scrape failed (can't list articles) |
| 3 | Vector store unreachable / > 10% of uploads failed |

Brief requires: `docker run -e API_KEY=... <image>` runs **once and exits 0**.

## CLI flags (optional, nice to have)
`--dry-run`, `--limit N`, `--scrape-only` (write .md without uploading — useful for spec 02 iteration).

## Logging
- stdlib `logging`, one line per event, `INFO` default. Format: `%(asctime)s %(levelname)s %(name)s %(message)s`.
- Never log the API key or full request headers.
- One final `RUN SUMMARY` line — this is what graders look for in job logs.

## Dockerfile
```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && chown -R app /app
USER app
CMD ["python", "main.py"]
```
- `PYTHONUNBUFFERED=1` so logs stream to the platform in real time.
- Pin versions in `requirements.txt` (`openai`, `requests`, `beautifulsoup4`, `markdownify`, `tiktoken`, `python-dotenv`).
- `.dockerignore`: `.env`, `.git`, `articles/`, `__pycache__`, `tests/` (optional).

## Env
- `.env.sample` committed with every var from spec 00, empty values.
- `.env` gitignored. `python-dotenv` loads it locally; in Docker/CI pass via `-e` / secrets.
- Accept both `OPENAI_API_KEY` and `API_KEY`.

## Acceptance criteria
- [ ] `docker build -t kb-sync .` succeeds.
- [ ] `docker run --rm -e API_KEY=sk-... kb-sync` runs once, prints RUN SUMMARY, `echo $?` → `0`.
- [ ] Second run shows only skips.
- [ ] `docker run --rm kb-sync` (no key) exits 1 with a clear message.
