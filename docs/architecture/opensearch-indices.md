# OpenSearch Indices

MineLogX AI uses **Amazon OpenSearch Serverless** with a VECTORSEARCH collection.
Two indices store vectors for different data domains.

---

## Collection

| Property | Value |
|---|---|
| Collection name | `minelogx-<env>-vectors` |
| Collection type | `VECTORSEARCH` |
| Region | `us-east-1` |

---

## Indices

### `csv_telemetry_vecs` — Fleet Telemetry

Stores vector embeddings of mining equipment telemetry data (GPS, fuel, haul cycles,
tire pressure, fatigue events, safety events).

| Property | Value |
|---|---|
| Embedding model | `cohere.embed-multilingual-v3` |
| Dimensions | 1024 |
| kNN engine | Faiss |
| Algorithm | HNSW |
| Search type | Hybrid (kNN + BM25) |
| Source data | 15 CSV files across C1, C2, C3 client datasets |
| Current doc count | Check with `uv run fab opensearch.status dev` |

Written by the **CSV Pipeline Lambda** (`minelogx-<env>-csv`).
Read by the **API Lambda** (`minelogx-<env>-api`) for fleet analysis and `/analyze` endpoint.

---

### `pdf_legal_vecs` — Regulatory Documents

Stores vector embeddings of legal documents extracted from PDFs across 3 jurisdictions
(Senegal, United States, Chile).

| Property | Value |
|---|---|
| Embedding model | `amazon.titan-embed-text-v2:0` |
| Dimensions | 1536 |
| kNN engine | Faiss |
| Algorithm | HNSW |
| Search type | Hybrid (kNN + BM25) |
| Source data | PDF regulatory documents uploaded to legislation bucket |
| Current doc count | Check with `uv run fab opensearch.status dev` |

Written by the **PDF Pipeline Lambda** (`minelogx-<env>-pdf`).
Read by the **API Lambda** for RAG compliance Q&A (`/chat` endpoint).

---

## Checking Index Status

```bash
# Collection health + document counts for both indices (default: dev)
uv run fab opensearch.status dev

# Same for another environment
uv run fab opensearch.status qa
```

Output includes collection status (ACTIVE/CREATING) and document count per index.
Saves a formatted log to `.fab-logs/opensearch-status-<env>-<ts>.log`.

---

## Re-ingesting All Documents

```bash
# Re-run the CSV pipeline on all S3 files (parallel)
uv run fab lambda.invoke-all csv dev --parallel

# Re-run the PDF pipeline on all S3 files (serial — PDF is event-driven so serial is safer)
uv run fab lambda.invoke-all pdf dev

# Or use the combined reindex task
uv run fab opensearch.reindex dev
```

---

## Important Notes

- The **AOSS vector indices are data-plane objects** — they are created by the ingest Lambdas
  at runtime, not by CloudFormation. If you delete and recreate the OpenSearch collection,
  the indices must be repopulated by running the pipelines.
- Both indices use **hybrid search** (kNN + BM25). Pure vector search is not used in production
  because BM25 improves precision for queries with specific entity names or document references.
- **Cross-index queries are not supported** — each RAG call targets either `csv_telemetry_vecs`
  or `pdf_legal_vecs` based on the intent of the query.
