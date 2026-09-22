# Spec 03 — Vector Store Uploader & Chunking (`bot/vector_store.py`)

Worth **20 points**. **Must be API-based** — no UI drag-and-drop.

## API surface (OpenAI Python SDK, current)
- Upload file: `client.files.create(file=..., purpose="assistants")`
- Vector store: `client.vector_stores.create / retrieve / list`
- Attach file: `client.vector_stores.files.create(vector_store_id, file_id, attributes=..., chunking_strategy=...)`, or `file_batches.create_and_poll` for bulk
- List attached: `client.vector_stores.files.list(vector_store_id)` (paginate)
- Remove: `client.vector_stores.files.delete(...)` **and** `client.files.delete(file_id)` (otherwise orphan files accumulate in storage)

Do **not** use `client.beta.assistants` / `threads` — sunset 2026-08-26.

## Vector store resolution
1. If `VECTOR_STORE_ID` set → retrieve it (fail if missing).
2. Else list stores, pick one named `VECTOR_STORE_NAME`; create if none.
3. Log the ID (needed for the Playground).

## Per-file attributes (max 16 keys; string values ≤ 512 chars)
```json
{
  "article_id": "360012345678",
  "slug": "360012345678-how-to-add-youtube",
  "content_hash": "sha256:…",
  "source_updated_at": "2026-09-01T10:00:00Z",
  "url": "https://support.optisigns.com/hc/en-us/articles/…"
}
```
These double as the sync state (spec 04). `url` also enables attribute filtering later.

## Chunking strategy
Use static chunking, set explicitly (don't rely on `auto`):
```python
{"type": "static", "static": {"max_chunk_size_tokens": 800, "chunk_overlap_tokens": 200}}
```
API limits: chunk size 100–4096; overlap ≤ half of chunk size.

Rationale to put in README:
- Help-center articles are short, single-topic, step-by-step. Most fit in 1–3 chunks of 800 tokens, so a step list rarely gets split mid-procedure.
- 200 overlap (vs default 400) cuts duplicated embeddings while still carrying a heading/context across boundaries.
- Title + `Article URL:` header sits in chunk 1, so retrieval of the first chunk always yields a citable URL. (Trade-off: later chunks lack the URL — optional mitigation: repeat URL at end of doc.)

## Chunk count logging (trap)
The API does **not** return chunk counts. Compute locally:
- Tokenize each `.md` with `tiktoken` (`o200k_base`).
- Estimate chunks = `1` if `n ≤ size`, else `ceil((n - overlap) / (size - overlap))`.
- Log per run: `files_embedded=N chunks_estimated=M` and say in README that it's an estimate mirroring the configured static strategy.

## Upload procedure
- Upload new files in a batch (`file_batches.create_and_poll`) — or concurrent single uploads with a small worker pool (≤ 5) if per-file attributes are needed at attach time. Per-file `files.create` on the vector store accepts `attributes` directly — simplest.
- After attach, poll until `status == "completed"`; count `failed` and log `last_error`.
- Upload the file with its `slug.md` filename so citations/annotations show a readable name.

## Interface
```python
class VectorStore:
    def __init__(self, client, store_id): ...
    def list_indexed(self) -> dict[str, IndexedFile]   # key = article_id
    def add(self, path: Path, attrs: dict) -> str       # returns vs file id
    def replace(self, old: IndexedFile, path: Path, attrs: dict) -> str
    def remove(self, old: IndexedFile) -> None

def estimate_chunks(text: str, size: int, overlap: int) -> int
```

## Acceptance criteria
- [ ] First run uploads all articles; log shows `files=N chunks≈M`.
- [ ] Vector store in the dashboard shows N completed files.
- [ ] No API key in code or git history.
- [ ] Failed file attach is logged but doesn't abort the whole run (exit code policy in spec 05).
