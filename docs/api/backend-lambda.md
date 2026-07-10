# Backend Lambda Architecture

Three Lambda functions back the platform. All are deployed from `onprem-aws/backend/`.

---

## Function Table

| Function | Handler | Trigger |
|---|---|---|
| `minelogx-<env>-api` | `handler.lambda_handler` | HTTP API Gateway v2 |
| `minelogx-<env>-csv` | `csv_pipeline.lambda_function.lambda_handler` | Step Functions (per stage) |
| `minelogx-<env>-pdf` | `pdf_pipeline.agent.pdf_vectorization_pipeline.lambda_handler` | EventBridge (S3 ObjectCreated `.pdf`) |

---

## API Lambda (`minelogx-<env>-api`)

**Location:** `onprem-aws/backend/lambdas/api/handler.py`

Routes all HTTP API v2 requests. Strips the `/{stage}` prefix from the raw path before routing.

**GET routes** — read from S3, no Bedrock call:

| Route | Source |
|---|---|
| `GET /health` | Health check |
| `GET /kpis` | S3 curated data via `csv_loader` |
| `GET /fleet/assets` | S3 curated data |
| `GET /fuel/records` | S3 curated data |
| `GET /fuel/trend` | S3 curated data |
| `GET /maintenance/items` | S3 curated data |
| `GET /maintenance/work-orders` | S3 curated data |
| `GET /telemetry/gps` | S3 curated data |
| `GET /telemetry/zones` | S3 curated data |

**POST routes** — invoke Bedrock agents:

| Route | Agent | Index |
|---|---|---|
| `POST /analyze` | `FleetAgent` (Data Analysis) | `csv_telemetry_vecs` |
| `POST /chat` | `BedrockRAGAgent` (Compliance) | `pdf_legal_vecs` |

Agent singletons are module-level (initialized once per warm invocation for performance).

---

## CSV Lambda (`minelogx-<env>-csv`)

**Location:** `onprem-aws/backend/csv_pipeline/`

Invoked once per stage by the Step Functions state machine. Stages:

1. `schema_inspection` — infer canonical field mappings via Bedrock Claude
2. `normalize` — apply mapping + format normalization
3. `chunk` — create semantic chunks + annotate with Claude
4. `ingest` — embed with Cohere + bulk ingest to OpenSearch

Layer: `minelogx-<env>-csv-deps` (built with `uv run fab lambda.build-layer csv`).

---

## PDF Lambda (`minelogx-<env>-pdf`)

**Location:** `onprem-aws/backend/pdf_pipeline/`

A single function that collapses the full pipeline into one `run_pipeline()` call:

1. Classify document (Haiku 4.5)
2. Extract text (Textract for dense PDFs, Claude for complex layouts)
3. Embed sections (Titan 1536d)
4. Ingest to `pdf_legal_vecs`

Layer: `minelogx-<env>-pdf-deps` (built with `uv run fab lambda.build-layer pdf`).

!!! note "Future refactor"
    Splitting the PDF pipeline into multiple functions (classify → extract → embed) is tracked
    as a future improvement if throughput or timeout demands it. The current single-Lambda
    approach simplifies IaC at the cost of granularity.

---

## Runtime Configuration

| Property | Value |
|---|---|
| Runtime | Python 3.11 |
| Minimum timeout | 5 minutes (AI-heavy functions) |
| Default memory | 256 MB (512 MB+ for PDF) |
| Logging | Structured JSON to CloudWatch |

```bash
# Check current runtime config for all functions
uv run fab lambda.status dev

# Tail CloudWatch logs
uv run fab lambda.logs api dev --follow
```

---

## Redeploying Code

```bash
# Re-zip and push Lambda code without rebuilding layers
uv run fab lambda.redeploy api dev

# With versioning (required before lambda.rollback works)
uv run fab lambda.redeploy api dev --publish
```
