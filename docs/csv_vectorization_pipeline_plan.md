# MineLogX-AI — CSV Vectorization Pipeline Implementation Plan

**Date:** 2026-06-18
**Branch:** `feature/simple_rag`
**Based on:** current codebase review, Anthropic model documentation (Claude Sonnet 4.6: 1M token
context), AWS Bedrock Cohere Embed V4 documentation, and the approved smart hybrid strategy.

---

## Governing principles

1. **Existing code is untouched.** `pipeline.py`, `orchestrator.py`, `bedrock_orchestrator.py`
   continue to serve the Data Analysis Layer (dashboard KPIs). This plan adds a parallel pipeline
   with no regressions.
2. **Durable S3 artifacts at every stage.** Each stage writes its output to S3 before the next
   stage reads it. Stages are independently retriable.
3. **LLM for reasoning, Python for computation.** Claude is called once per file in Stage 1.
   Statistics, anomaly detection, chunking, and ingestion are pure Python/pandas.
4. **Recipe-driven normalization.** Stage 2 executes instructions produced by Stage 1 — it does
   not switch on pre-defined format types.
5. **`tool_choice` everywhere Claude outputs structured data.** No raw text generation +
   JSON parsing fallbacks.
6. **Cohere Embed V4 as the embedding model.** `cohere.embed-v4:0` at 1024 dims / int8 for all
   CSV telemetry vectors. The RAG agent must use the same model at query time.

---

## S3 artifact layout

```
{bucket}/{prefix}/
  raw/                                        ← source CSVs (existing, untouched)
  vectorization/
    {folder}/
      schema/{filename}.schema.json           ← Stage 1 output
      canonical/{filename}.parquet            ← Stage 2 output
      chunks/{filename}.chunks.jsonl          ← Stage 3 output (newline-delimited JSON)
```

---

## Stage 1 — Schema Inspector

**Goal:** Produce a `schema_descriptor.json` containing column classifications, accurate statistics,
detected structural anomalies, and an LLM-reasoned transformation recipe — without ever loading
the full CSV into memory.

### Step 1a — Streaming statistics + anomaly detection (pure Python)

**New file: `tools/csv_sampler.py`**

Streams through the full file using `pd.read_csv(chunksize=2000)`. Computes per-column statistics
accumulated across all chunks:

- `type` — inferred from majority dtype across chunks
- `null_pct` — running null count / total rows
- `min`, `max`, `mean` — running Welford online algorithm for numerics
- `cardinality_estimate` — HyperLogLog approximation for categoricals
- `sample_values` — up to 10 unique values, first seen

During streaming, flags **structural anomalies** — rows that signal a format problem:

| Anomaly type | Detection rule |
|---|---|
| Embedded header | ≥ 50% of cell values match column-name patterns (alphabetic, underscore-separated) |
| Separator row | ≥ 80% of values are null or empty string |
| Type break | Numeric column contains non-numeric string (e.g. `"TOTAL"`, `"N/A"`) |
| Column count shift | Chunk where `len(columns)` differs from the first chunk |

Anomalous rows are stored by **index only** — raw content is fetched in Step 1b, not here.

**Size decision:** after streaming, if `row_count ≤ 10_000`, set `send_full_file = True`.
At ~300 chars/row and Claude Sonnet 4.6's 1M token context (~3.4M chars), this fits comfortably.

```python
# Public interface
def stream_and_profile(
    file_path: str,
    local_mode: bool = False,
    chunk_size: int = 2_000,
) -> StreamProfile:
    """
    Returns StreamProfile(
        row_count,
        column_count,
        column_stats,           # dict[col_name → ColumnStats]
        anomaly_row_indices,    # list[int]
        send_full_file,         # bool
    )
    """
```

### Step 1b — Smart sample builder

Still in `csv_sampler.py`. Builds the compact input sent to Claude:

- If `send_full_file=True`: return the full DataFrame (already in memory from streaming)
- Otherwise: read **first 50 rows** + **anomalous rows** (up to 100, by stored index) +
  **last 20 rows**, deduplicated and preserved in original order

The LLM input is always a **compact representation**, never raw full-file CSV text:

```
Column statistics (1 line per column):
  truck_id (categorical)        — cardinality≈42, sample: ["TRK-001","TRK-002","TRK-003"]
  shift_date (datetime)         — range: 2023-01-01 → 2024-06-30, null_pct=0.0%
  fuel_consumption_rate (float) — mean=124.3, min=0.0, max=891.2, null_pct=2.1%
  ...

Structural sample (170 rows total):
  [first 50 rows as compact CSV]
  [anomaly rows, labeled with original row index and anomaly type]
  [last 20 rows as compact CSV]

Total rows: 487,234
```

### Step 1c — One LLM call with `tool_choice`

**Modified file: `tools/column_mapper.py`** — add `inspect_schema_with_tool_use()`.

Replaces the current `_llm_complete()` + `_extract_json()` pattern for schema inspection.
Uses `tool_choice={"type": "tool", "name": "describe_csv_structure"}` — response is guaranteed
to conform to the schema, no fallback JSON parsing needed.

```python
INSPECT_TOOL = {
    "name": "describe_csv_structure",
    "description": "Analyse the structure of a CSV file sample and produce a transformation recipe.",
    "input_schema": {
        "type": "object",
        "required": ["column_classifications", "transformation_steps", "reasoning"],
        "properties": {
            "column_classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "role", "kpi_variable", "confidence"],
                    "properties": {
                        "name":         {"type": "string"},
                        "role":         {"type": "string",
                                         "enum": ["entity", "metric", "datetime",
                                                  "categorical", "segment_marker",
                                                  "metadata", "unknown"]},
                        "kpi_variable": {"type": ["string", "null"]},
                        "confidence":   {"type": "string",
                                         "enum": ["high", "medium", "low"]}
                    }
                }
            },
            "transformation_steps": {
                "type": "array",
                "description": "Ordered list of operations to normalize this file. Empty array means pass-through.",
                "items": {
                    "type": "object",
                    "required": ["operation", "params"],
                    "properties": {
                        "operation": {"type": "string"},
                        "params":    {"type": "object"}
                    }
                }
            },
            "has_structural_anomalies": {"type": "boolean"},
            "anomaly_description":      {"type": ["string", "null"]},
            "reasoning":                {"type": "string"}
        }
    }
}
```

`transformation_steps` is an **open list** — Claude produces whatever operations it believes are
needed. An empty array means pass-through. Claude is not constrained to any pre-defined type
taxonomy. Examples:

```json
// Clean flat CSV — no transformation needed
{ "transformation_steps": [] }

// File with 3 metadata rows at the top
{ "transformation_steps": [
    {"operation": "skip_rows", "params": {"n": 3}}
]}

// Pivoted segment file with trailer summary rows
{ "transformation_steps": [
    {"operation": "fill_forward",   "params": {"column": "segment_type"}},
    {"operation": "pivot_segments", "params": {"segment_col": "segment_type",
                                                "value_col": "reading"}},
    {"operation": "filter_rows",    "params": {"exclude_expr": "truck_id == 'TOTAL'"}}
]}
```

**Output: persisted to S3** as `vectorization/{folder}/schema/{filename}.schema.json`

```json
{
  "file_path":               "C1/fuel_management_events.csv",
  "schema_version":          "1.0",
  "produced_at":             "2026-06-18T10:00:00Z",
  "row_count":               487234,
  "column_count":            28,
  "column_stats":            { "...": "..." },
  "column_classifications":  [ "..." ],
  "transformation_steps":    [ "..." ],
  "has_structural_anomalies": false,
  "anomaly_description":     null,
  "reasoning":               "..."
}
```

**Modified file: `tools/schema_advisor.py`** — add `inspect_schema_sampled(file_path, local_mode)`.
Existing `discover_schema()` is untouched.

---

## Stage 2 — Format Normalizer → canonical_table.parquet

**Goal:** Execute the transformation recipe from `schema_descriptor.json` and write a clean,
flat, consistently typed parquet file to S3.

**New file: `tools/format_normalizer.py`**

Reads `schema_descriptor.json` from S3. Executes `transformation_steps` in order. Each operation
is a registered Python function:

```python
OPERATION_REGISTRY: dict[str, Callable[[pd.DataFrame, dict], pd.DataFrame]] = {
    "skip_rows":           op_skip_rows,            # drop first N rows
    "set_header_row":      op_set_header_row,        # promote row N to header
    "combine_header_rows": op_combine_header_rows,   # merge multi-level headers
    "transpose":           op_transpose,             # swap rows/columns
    "pivot_segments":      op_pivot_segments,        # long → wide on segment column
    "melt":                op_melt,                  # wide → long
    "filter_rows":         op_filter_rows,           # pandas query expression
    "rename_columns":      op_rename_columns,        # dict of old → new names
    "fill_forward":        op_fill_forward,          # ffill sparse segment headers
    "drop_columns":        op_drop_columns,          # remove columns by name
}
```

**Adding support for a new CSV format in the future requires one `op_*` function + one dict
entry.** No changes to Stage 1 prompt, no LLM retraining.

For large files (> 10k rows), normalization runs in streaming mode using
`pd.read_csv(chunksize=)` and writes incrementally with `pyarrow.parquet.ParquetWriter` —
memory stays bounded.

```python
# Public interface
def normalize(
    file_path: str,
    schema_descriptor: dict,
    local_mode: bool = False,
) -> NormalizeResult:
    """
    Returns NormalizeResult(
        output_s3_key,    # path of written parquet
        row_count,
        column_count,
        errors,           # list of step errors — pipeline continues gracefully
    )
    """
```

**Output: persisted to S3** as `vectorization/{folder}/canonical/{filename}.parquet`

---

## Stage 3 — Chunker + Serializer

**Goal:** Produce semantically coherent, self-contained text chunks with provenance metadata,
ready for embedding. No LLM call in this stage — serialization is deterministic Python.

**New file: `tools/chunker_serializer.py`**

Reads `canonical_table.parquet` from S3.

### Chunking strategies

**Time-window chunking** (default when `datetime_columns` is non-empty in schema descriptor):
- Group rows into windows of configurable duration (default: 7 days)
- Overlap of `overlap_rows=50` rows at window boundaries to avoid cutting mid-event
- Chunk size capped at `max_rows_per_chunk=500`

**Row-count chunking** (fallback when no datetime column exists):
- Fixed windows of `chunk_size=300` rows
- Overlap of `overlap_rows=30` rows

### NL serialization

Each chunk is serialized to compact natural language describing the aggregate content of that
window:

```
Shift data for truck TRK-042, 2024-03-15 06:00 – 2024-03-15 14:00 (42 rows).
  fuel_consumption_rate: mean=118.4 L/hr, max=412.3 L/hr, 2 readings above 300 L/hr
  payload_utilization: mean=84.2%, range=61.1%–98.7%
  idle_rate: mean=12.3%
  distance_km: total=187.4 km
  Outliers: fuel_consumption_rate=412.3 at 2024-03-15T08:23:00 (truck TRK-042)
```

### Chunk output schema

Written to S3 as newline-delimited JSON (`{filename}.chunks.jsonl`), one object per line:

```json
{
  "chunk_id":   "C1/fuel_management_events__chunk_0042",
  "text":       "Shift data for truck TRK-042...",
  "metadata": {
    "source_file":    "C1/fuel_management_events.csv",
    "folder":         "C1",
    "chunk_index":    42,
    "row_range":      [21000, 21042],
    "date_range":     {"start": "2024-03-15T06:00:00Z", "end": "2024-03-15T14:00:00Z"},
    "entity_values":  {"truck_id": "TRK-042"},
    "schema_version": "1.0",
    "column_list":    ["truck_id", "shift_date", "fuel_consumption_rate", "..."]
  }
}
```

```python
# Public interface
def chunk_and_serialize(
    file_path: str,
    schema_descriptor: dict,
    strategy: str = "time_window",
    window_days: int = 7,
    max_rows_per_chunk: int = 500,
    overlap_rows: int = 50,
    local_mode: bool = False,
) -> ChunkResult:
```

---

## Stage 4 — OpenSearch Ingestion

**Goal:** Ingest text chunks into OpenSearch with Cohere Embed V4 embeddings for hybrid
BM25 + kNN retrieval.

**New file: `tools/opensearch_ingestor.py`**

Reads `{filename}.chunks.jsonl` from S3. Bulk-ingests into OpenSearch using `opensearch-py`.
Embedding is handled at ingest time by the OpenSearch neural ingest pipeline — chunks are
sent as plain text; OpenSearch calls Bedrock `cohere.embed-v4:0` internally.

### Embedding model

| Parameter | Value | Rationale |
|---|---|---|
| Model | `cohere.embed-v4:0` | State-of-the-art text retrieval; Bedrock-native; aligns with architecture diagram |
| `input_type` | `search_document` | Correct for corpus indexing per Cohere docs |
| `output_dimension` | `1024` | Best quality/cost balance; avoids 1536's extra storage overhead |
| `embedding_types` | `int8` | Up to 83% vector storage reduction vs float; minimal retrieval quality loss |

**Critical dependency:** The RAG agent (`docs/RAG/rag_agent_EC2.py`) currently embeds queries
with `mxbai-embed-large` via Ollama. It **must** be updated to embed queries with
`cohere.embed-v4:0` at the same dimensions and type before this index can serve queries.
Query embeddings in a different vector space than document embeddings produce silently wrong
retrieval results.

### OpenSearch index mapping

```json
{
  "settings": {
    "default_pipeline": "text-embedding-pipeline",
    "index.knn": true
  },
  "mappings": {
    "properties": {
      "text":           {"type": "text"},
      "text_embedding": {
        "type":   "knn_vector",
        "dimension": 1024,
        "method": {"engine": "nmslib", "name": "hnsw",
                   "parameters": {"ef_construction": 128, "m": 16}}
      },
      "chunk_id":       {"type": "keyword"},
      "source_file":    {"type": "keyword"},
      "folder":         {"type": "keyword"},
      "chunk_index":    {"type": "integer"},
      "date_start":     {"type": "date"},
      "date_end":       {"type": "date"},
      "entity_values":  {"type": "object", "dynamic": true},
      "schema_version": {"type": "keyword"}
    }
  }
}
```

The `text-embedding-pipeline` is an OpenSearch ingest pipeline configured with the
`text_embedding` processor pointing to `cohere.embed-v4:0` on Bedrock.

Ingestion uses `helpers.bulk()` with `chunk_size=50` documents per request. Per-document errors
are captured and logged without stopping the batch.

### New file: `config/opensearch_settings.py`

```python
@dataclass
class OpenSearchConfig:
    host:            str = field(default_factory=lambda: os.getenv("OPENSEARCH_HOST", ""))
    port:            int = field(default_factory=lambda: int(os.getenv("OPENSEARCH_PORT", "443")))
    index_name:      str = field(default_factory=lambda: os.getenv(
                              "OPENSEARCH_INDEX", "minelogx-telemetry-v1"))
    embedding_model: str = "cohere.embed-v4:0"
    output_dimension: int = 1024
    embedding_type:  str = "int8"
    ingest_pipeline: str = "text-embedding-pipeline"
    bulk_batch_size: int = 50
```

Added to `config/settings.py` `AgentConfig` as `opensearch: OpenSearchConfig`.

---

## New orchestrator: `agent/csv_vectorization_pipeline.py`

`CSVVectorizationPipeline` — runs Stages 1–4 per file. Completely separate from
`FolderPipeline` (which continues to serve the dashboard analytics use case).

**Idempotency:** before running each stage, checks if the S3 artifact already exists.
If yes, loads from S3 and skips that stage. Reruns only execute failed or missing stages.

**Per-stage error isolation:** same `_call()` pattern from `pipeline.py` — exceptions are
captured, logged, and stored in a run report. The pipeline continues to the next file.

```python
class CSVVectorizationPipeline:
    def run(
        self,
        folder: str,
        *,
        force: bool = False,
        output_path: str | None = None,
    ) -> VectorizationReport:
        """
        Process all CSVs in folder through Stages 1–4.
        force=True re-runs all stages even if S3 artifacts already exist.
        Returns VectorizationReport(folder, file_count, per_file_results, errors).
        """
```

---

## Summary of all file changes

| File | Action | Stage |
|---|---|---|
| `tools/csv_sampler.py` | **New** — streaming profiler + smart sample builder | 1 |
| `tools/format_normalizer.py` | **New** — recipe executor with operation registry | 2 |
| `tools/chunker_serializer.py` | **New** — time-window chunker + NL serializer | 3 |
| `tools/opensearch_ingestor.py` | **New** — Cohere Embed V4 bulk ingestion | 4 |
| `config/opensearch_settings.py` | **New** — OpenSearch connection + index config | 4 |
| `agent/csv_vectorization_pipeline.py` | **New** — Stage 1–4 orchestrator | All |
| `tools/schema_advisor.py` | **Extend** — add `inspect_schema_sampled()` | 1 |
| `tools/column_mapper.py` | **Extend** — add `inspect_schema_with_tool_use()` | 1 |
| `config/settings.py` | **Extend** — add `OpenSearchConfig` dataclass | 4 |
| `docs/RAG/rag_agent_EC2.py` | **Update (dependency)** — migrate query embedding from `mxbai-embed-large` to `cohere.embed-v4:0` at 1024 dims / int8 | — |
| `pipeline.py`, `orchestrator.py`, `bedrock_orchestrator.py` | **Unchanged** | — |

---

## Implementation order

| Phase | Deliverable | Why this order |
|---|---|---|
| **1** | `csv_sampler.py` + streaming stats + anomaly detection | Foundation — all other stages depend on the profile |
| **2** | `column_mapper.py` + `schema_advisor.py` extensions (tool_use call) | Stage 1 LLM call — independently testable with local sample data |
| **3** | `format_normalizer.py` + operation registry | Stage 2 — testable against known-format files without OpenSearch |
| **4** | `chunker_serializer.py` | Stage 3 — testable end-to-end without OpenSearch |
| **5** | `opensearch_ingestor.py` + `opensearch_settings.py` + `settings.py` | Stage 4 — requires infrastructure |
| **6** | `csv_vectorization_pipeline.py` orchestrator | Ties all stages together |
| **7** | `rag_agent_EC2.py` embedding migration | Must align with index before queries can work |
