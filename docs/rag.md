# Document library (agentic RAG) and clarifying questions

## Flow
```
upload (web) → POST /documents → raw file to ArtifactStore → DocumentIngest workflow → librarian.ingest_document
                                                                            parse → describe figures → chunk → contextualise → embed → pgvector
job (POST /jobs {prompt, document_ids}) → SimulationJob:
  clarifier.clarify ──► awaiting_clarification (signal `clarify`: answers | use_defaults; timeout → defaults)
  researcher.research (agentic: search_documents + MCP tools + web search, loops until satisfied) → modeler → …
```

## Storage: `packages/omnific-rag` (package `omnific_rag`)
Postgres 16 + pgvector (compose: `pgvector/pgvector:pg16`; GKE: the Cloud SQL instance, database `omnific_rag`,
`CREATE EXTENSION vector`). Env `RAG_DATABASE_URL` (a libpq URL). Schema (created idempotently by `omnific_rag.db.migrate()`):

```sql
documents(id text pk, filename, content_type, title, pages int, status text  -- pending|ingesting|ready|failed
          , error text, n_chunks int, n_figures int, n_tables int, created_at timestamptz, meta jsonb)
chunks(id bigserial pk, document_id text fk on delete cascade, ord int, kind text  -- text|table|figure
       , page int null, section text, context text, content text, embedding vector(1536)
       , tsv tsvector generated always as (to_tsvector('english', coalesce(section,'') || ' ' || coalesce(context,'') || ' ' || content)) stored)
index: hnsw (embedding vector_cosine_ops); gin (tsv); btree (document_id)
```

`omnific_rag.search(query, document_ids, k=8, kinds=None) -> list[Hit]` is hybrid: the top 40 by cosine and the top 40 by
`ts_rank_cd` over the query's lexemes ORed together (`websearch_to_tsquery` with `&` turned into `|`), fused with reciprocal-rank fusion (k = 60). It returns
`Hit{document_id, title, page, kind, section, content, score, citation_url}`. Only the given `document_ids` are ever
searched (scoping is mandatory: no global search).

Citation URL for document facts: `omnific://doc/<document_id>#page=<n>`. The web UI resolves it to the document page.

## Ingestion (librarian agent)
1. **Parse** to an ordered list of blocks `{kind, page, section, text | image}`:
   - PDF: pdfplumber for text with heading detection (font size and boldness) and tables (as Markdown). For pages with
     raster images or substantial vector drawings, pypdfium2 renders the page (150 dpi) as a figure candidate.
   - DOCX (python-docx: headings, paragraphs, tables, inline images); PPTX (python-pptx: slide titles, text, tables,
     pictures); XLSX/CSV (openpyxl/csv: each sheet as a Markdown table, capped rows); HTML (BeautifulSoup:
     headings, text, tables); MD/TXT; PNG/JPEG/WebP (the whole image is a figure).
2. **Figures and graphs**: vision (`MODEL_GENERAL`, `LLM.describe_image`) with a prompt asking for the figure type, axes and
   units, series, trends, notable values read off the chart, and the caption. Each figure becomes a `figure` chunk. Capped
   at `RAG_MAX_FIGURES` (default 40) per document; the rest are skipped with a warning.
3. **Agentic chunking**: structure first (split on headings; tables and figures stay whole), then pack ~350–700 tokens with
   one paragraph of overlap. For each chunk, `MODEL_FAST` writes a 1–2 sentence `context` situating it in the document
   ("From §3.2 of <title>, discussing …"). This is contextual retrieval: the context is embedded with the chunk.
   It also proposes the document `title` if the file lacks one.
4. **Embed** `section + context + content` (`LLM.embed`, `MODEL_EMBEDDING=text-embedding-3-large`,
   `EMBEDDING_DIMENSIONS=1536`) and insert. Status goes `ingesting → ready | failed`, with the error.
Limits: `RAG_MAX_UPLOAD_MB` (default 50). Unsupported types are rejected at upload.

## Agentic retrieval (researcher)
`LLM.run_tools` with function tools:
- `search_documents(query, kinds?)`: hybrid search over the job's `document_ids`. The model reformulates and repeats
  queries as needed (max steps 8).
- `read_chunk_neighbors(document_id, ord)`: the surrounding chunks, for more context.
- MCP tools from the Docker MCP gateway (`MCP_GATEWAY_URL`, streamable HTTP), exposed as function tools named
  `mcp__<server>__<tool>`. Optional: with the variable unset there are no MCP tools.
- The hosted `web_search` tool, as before.
Instructions: prefer the user's documents over the web, cite the document and page for every fact taken from them, and
report contradictions between sources in `open_questions`.

## Clarifying questions (clarifier agent, before research)
`clarify(ClarifyInput) -> ClarifyOutput` (`MODEL_GENERAL`). It asks only about choices that **materially change the
simulation**: environment (where, altitude, conditions), geometry and dimensions, materials, initial conditions,
what to measure, the time span, and the fidelity wanted. That's at most 6 questions, and none at all for an unambiguous
prompt. Each question has a sensible `default`. Questions whose answer is in the attached document titles are skipped.

The workflow sets `awaiting_clarification` (query `questions`), then waits for the `clarify` signal or
`clarify_timeout_hours`. Answers are matched to questions by id. Missing answers, `use_defaults` and a timeout all take
the default, with `used_default=true`. Clarifications go to the researcher and the modeler. The modeler treats
answers as binding, and every default it uses becomes an explicit assumption ("Assumed <default> for <question> (not
specified by the user)").

API: `GET /jobs/{id}` includes `questions` while awaiting. `POST /jobs/{id}/clarify` takes a `ClarifyAnswers` body
(409 unless awaiting). `POST /documents` (multipart), `GET /documents`, `GET /documents/{id}` (status) and
`DELETE /documents/{id}`.

## `omnific_rag` Python API (async, psycopg 3; the shared contract for the librarian, researcher and API)
```python
from omnific_rag import db, search
await db.migrate()                                                    # idempotent; CREATE EXTENSION vector, tables, indexes
await db.create_document(id, filename, content_type, meta={}) -> Document      # status=pending
await db.get_document(id) -> Document | None
await db.list_documents(limit=100) -> list[Document]
await db.delete_document(id) -> bool                                  # cascades to chunks
await db.set_status(id, status, error=None, **counts)                 # counts: title, pages, n_chunks, n_figures, n_tables
await db.replace_chunks(document_id, chunks: list[ChunkIn]) -> int    # delete+insert in one transaction
await search.search(query, document_ids, k=8, kinds=None) -> list[Hit]  # embeds the query with LLM.embed
await search.neighbors(document_id, ord, span=1) -> list[Hit]
# pydantic models in omnific_rag.models: Document, ChunkIn{ord, kind, page, section, context, content, embedding}, Hit
```
Connection: `RAG_DATABASE_URL`, one shared `psycopg_pool.AsyncConnectionPool` per process (`db.pool()`), with pgvector
types registered on connect. Tests use `RAG_TEST_DATABASE_URL` and skip when it is unset.
