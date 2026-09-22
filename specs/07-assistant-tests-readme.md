# Spec 07 — Assistant Setup, Tests, README & Deliverables

## A. Assistant (OptiBot)
Created in the OpenAI Playground UI (allowed by the brief), backed by the API-populated vector store.

1. Playground → new prompt/agent config, choose a current model.
2. Instructions — paste **verbatim**, no edits:
```
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```
3. Tools → File search → select the vector store by ID (from main.py log).
4. Ask: **"How do I add a YouTube video?"** → screenshot showing bullets + `Article URL:` citations.
5. Take 1–2 more screenshots with other questions (e.g., "How does caching work?") — brief says "sample questions" (plural) in deliverables.

Note: OpenAI has flagged reusable prompt objects for deprecation too. Screenshot is what's graded, but as a backup add `scripts/ask.py`, which calls `client.responses.create(model=..., instructions=SYSTEM_PROMPT, tools=[{"type": "file_search", "vector_store_ids": [VS_ID]}], input=question)` and prints the answer + cited filenames. Keep the prompt text in one constant, byte-identical to the brief.

If the answer lacks URLs: check the `Article URL:` line is present in the .md (spec 02) — that's the usual cause.

## B. Tests (+5 bonus) — `pytest`, no network
| Test | What |
|---|---|
| `test_markdown.py` | Fixture HTML (headings, code, table, image, iframe, empty divs, `&nbsp;`, relative link) → expected MD snapshot; determinism (twice → same bytes); header contains `Article URL:` |
| `test_slug.py` | Slug from `html_url`; special chars; uniqueness |
| `test_sync_plan.py` | `plan()` for add / update / skip / remove / duplicate / safety-guard (empty scrape → no deletes) |
| `test_chunks.py` | `estimate_chunks` edge cases (short doc = 1, exact boundary, long doc) |
| `test_zendesk.py` | Pagination with a mocked session (2 pages → all articles), 429 retry |

Add a CI step running `pytest` on push (same workflow file or a separate one).

## C. README (≤ 1 page)
Sections, in order:
1. **What it does** — 2 lines.
2. **Setup** — `cp .env.sample .env`, fill key; `pip install -r requirements.txt`.
3. **Run locally** — `python main.py` / `python main.py --dry-run` / Docker commands.
4. **How it works** — scraper (Zendesk API, why no nav/ads), Markdown rules, delta (hash in vector-store attributes), chunking strategy + rationale (spec 03), chunk count is an estimate.
5. **Daily job** — platform, schedule, **link to logs**.
6. **Screenshot** — embedded image of the YouTube answer with citations.
7. **Trade-offs / what I cut** — the brief explicitly invites this.

## D. Repo hygiene
- Repo name cryptic, **no "optisigns"** in it (e.g., `kb-lantern`, `doc-ferry`).
- Small, meaningful commits following the build order (scraper → converter → uploader → delta → docker → deploy → tests → readme).
- `.env.sample` present; `.env` and `articles/` gitignored (optionally commit 3–5 sample `.md` so reviewers can judge quality without running).
- Check `git log -p | grep -i "sk-"` returns nothing before pushing.

## Final submission checklist
- [ ] ≥ 30 clean `.md` files produced
- [ ] Upload via Python API, files + chunk counts logged
- [ ] Assistant with verbatim prompt; screenshot(s) with citations
- [ ] `main.py` + Dockerfile; `docker run -e API_KEY=...` exits 0
- [ ] Daily schedule live; log link works; RUN SUMMARY shows added/updated/skipped
- [ ] README ≤ 1 page with setup, local run, log link, screenshot
- [ ] Tests pass
- [ ] Repo name doesn't contain "optisigns"; no keys committed
