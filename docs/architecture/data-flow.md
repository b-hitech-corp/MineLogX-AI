# Data Flow

This page describes how data moves through MineLogX AI — from raw IoT input to
AI-ready vectors and query responses.

---

## S3 Lifecycle (Telemetry Bucket)

All telemetry data flows through a prefix-controlled lifecycle. Each prefix represents a trust level.

```
s3://minelogx-<env>-telemetry-data/

raw/              ← Untrusted incoming data — NEVER read directly by any AI model
    ↓ (validation Lambda)
quarantine/       ← Failed validation or guardrail checks — not processed further
approved/         ← Passed validation — safe for AI pipeline consumption
    ↓ (guardrail check via Bedrock Guardrails)
vector-input/     ← Guardrail-passed chunks — ready for embedding
    ↓ (embedding pipeline: CSV Lambda + Cohere)
OpenSearch        ← csv_telemetry_vecs — indexed, searchable

logs/
  ├── guardrails/
  ├── validation/
  ├── embedding/
  └── opensearch-ingest/
```

!!! danger "Critical rule"
    Nothing from `raw/` goes directly to Bedrock embedding models or OpenSearch.
    All data must pass validation and guardrail checks before reaching `vector-input/`.

---

## CSV Telemetry Pipeline (Batch)

Triggered daily by EventBridge Scheduler or manually with `uv run fab lambda.invoke-all csv dev`.

```
S3 (telemetry-data bucket)
  └── C1/, C2/, C3/  — 15 CSV files across 3 client sites

  ↓  EventBridge Scheduler (daily) or fab lambda.invoke-all csv dev

Step Functions: minelogx-<env>-csv-pipeline
  ├── Stage 1 — Schema Inspection
  │     Lambda CSV reads CSV headers and samples rows
  │     Bedrock Claude infers canonical field mappings
  │
  ├── Stage 2 — Normalization
  │     Lambda CSV applies schema mapping + format normalization
  │     Writes normalized data to approved/
  │
  ├── Stage 3 — Chunking + Annotation
  │     Lambda CSV chunks rows into semantic segments
  │     Bedrock Claude annotates each chunk with business context
  │
  └── Stage 4 — OpenSearch Ingest
        Cohere embed-multilingual-v3 embeds each chunk (1024d)
        Lambda CSV bulk-ingests to csv_telemetry_vecs
```

**Current status:** 15/15 CSV files SUCCEEDED across C1, C2, and C3 datasets.

---

## PDF Legal Document Pipeline (Event-driven)

Triggered automatically on S3 PutObject or manually with `uv run fab lambda.invoke-all pdf dev`.

```
s3://minelogx-<env>-legislation-documents/
  └── *.pdf  (regulatory documents — Senegal, US, Chile)

  ↓  EventBridge Rule (S3 ObjectCreated *.pdf)

Lambda PDF: minelogx-<env>-pdf  (run_pipeline — single function, 4 internal stages)
  ├── Signal 1 — PDF Classification
  │     Haiku 4.5 classifies document type and jurisdiction
  │
  ├── Signal 2 — Text Extraction
  │     Amazon Textract for scanned/dense PDFs
  │     Bedrock Claude Sonnet 4.6 for complex layouts requiring comprehension
  │
  ├── Signal 3 — Section Embedding
  │     Amazon Titan Embed Text v2 (1536d)
  │
  └── Signal 4 — OpenSearch Ingest
        Bulk-ingests section vectors to pdf_legal_vecs
```

!!! note "PDF pipeline limitations"
    PDFs exceeding ~100 pages hit the Bedrock extraction limit. PDFs requiring heavy Textract
    processing may timeout at 15 minutes (Lambda max). Both cases are tracked in `.fab-logs/`
    and CloudWatch — check with `uv run fab lambda.pdf-async-status`.

---

## Query Flow (Runtime — Chat and Analysis)

When a user sends a message via the chat interface:

```
User query (POST /chat)
  ↓
API Lambda: minelogx-<env>-api
  ↓
Bedrock Guardrails  — evaluate query (prompt injection, PII, topic denial)
  ↓  [if BLOCKED → return rejection message]
  ↓  [if PASSED]
OpenSearch Serverless  — hybrid kNN + BM25 retrieval
  ├── pdf_legal_vecs  (for compliance questions)
  └── csv_telemetry_vecs  (for fleet / telemetry questions)
  ↓
Amazon Bedrock  — Claude Sonnet 4.6 grounded generation
  (retrieved chunks passed as context)
  ↓
Bedrock Guardrails  — evaluate response (PII, off-topic)
  ↓
JSON response with citations → Browser
```

---

## OpenSearch Index Configuration

| Index | Pipeline | Embedding model | Dimensions | Search type |
|---|---|---|---|---|
| `csv_telemetry_vecs` | CSV Lambda | Cohere embed-multilingual-v3 | 1024 | kNN (HNSW/Faiss) + BM25 |
| `pdf_legal_vecs` | PDF Lambda | Amazon Titan Embed Text v2 | 1536 | kNN (HNSW/Faiss) + BM25 |

Both indices use HNSW with Faiss engine. Hybrid search combines vector similarity
with BM25 term-based scoring for better retrieval precision.
