# Spec 04 — Delta Detection & State (`bot/sync.py`)

## The core problem
The daily job runs in a fresh, stateless container. Any local `state.json` is gone after the run. Delta detection needs **persisted** state.

## Chosen approach: state lives in vector-store file attributes
On each run, list files in the vector store and read their `attributes` (`article_id`, `content_hash`). That list *is* the state. No DB, no bucket.

Alternatives (mention in README as considered):
- JSON state file in object storage (DO Spaces / S3) — extra infra and credentials.
- Commit `state.json` back to the repo from CI — noisy history, write permissions.

## Change signal
Use **sha256 of the generated Markdown**, not `updated_at` alone:
- Catches changes that matter to the bot (content), ignores metadata-only edits.
- Also catches converter changes (if you improve spec 02, all affected files re-upload — desirable).
- Keep `source_updated_at` in attributes for logging/debug; optional fast path: if `updated_at` unchanged **and** hash unchanged → skip.

## Classification
For each scraped article `a` with markdown hash `h`:
| Condition | Action | Counter |
|---|---|---|
| `a.id` not in index | upload | `added` |
| in index, hash differs | upload new, then delete old vs-file + file | `updated` |
| in index, hash same | nothing | `skipped` |

For each indexed file whose `article_id` is not in the scrape:
| Condition | Action | Counter |
|---|---|---|
| article disappeared | delete (only if scrape was complete and non-empty) | `removed` |

Safety guard: if scrape returned 0 articles or fewer than 50% of indexed count, **do not delete anything**; log a warning. Prevents wiping the store on an API hiccup.

Duplicate guard: if the index holds >1 file for the same `article_id` (e.g., a crashed previous run), keep the newest, delete the rest, log it.

## Ordering for updates
Upload new → confirm `completed` → delete old. Never leave an article missing from the store mid-run.

## Interface
```python
@dataclass
class SyncPlan:
    to_add: list[Doc]
    to_update: list[tuple[Doc, IndexedFile]]
    to_skip: list[Doc]
    to_remove: list[IndexedFile]

def plan(docs: list[Doc], index: dict[str, IndexedFile]) -> SyncPlan   # pure, no I/O
def apply(plan: SyncPlan, store: VectorStore, dry_run: bool) -> RunStats
```
Keep `plan()` pure — it's the most valuable unit test target.

## Run summary (log + artifact)
```
RUN SUMMARY scraped=412 added=3 updated=1 skipped=408 removed=0 failed=0 chunks_embedded≈9 duration=41s
```
Also write `artifacts/last_run.json` with the same fields plus lists of added/updated slugs.

## Acceptance criteria
- [ ] Second consecutive run: `added=0 updated=0 skipped=N`.
- [ ] Manually edit one indexed file's hash attribute (or change converter output for one article) → next run reports `updated=1`, store still has exactly N files.
- [ ] `DRY_RUN=true` prints the plan with no writes.
