# Containers — C4 Level 2

This page describes the **containers** (applications and services) that make up MineLogX AI on AWS,
following the [C4 model](https://c4model.com/) Container level.

A "container" in C4 terms is any separately deployable unit — a Lambda function, a managed service,
a web app, or a data store.

---

## Container Inventory

| Container | Technology | Responsibility |
|---|---|---|
| **Frontend** | React 19 / Vite / AWS Amplify | Dashboard (fleet, fuel, KPIs, GPS, maintenance), chat UI. Served as a static SPA via Amplify CDN. |
| **API Gateway** | HTTP API Gateway v2 | HTTPS entry point. Routes all requests to the API Lambda. Enforces CORS (`AllowOrigins: ['*']`). 29-second timeout limit applies here. |
| **API Lambda** | Python 3.11, `minelogx-<env>-api` | GET endpoints — reads from S3 (no LLM call). POST `/chat` — RAG agent over OpenSearch. POST `/analyze` — fleet data analysis agent. |
| **CSV Pipeline — Step Functions** | AWS Step Functions | State machine orchestrating the 4-stage CSV vectorization pipeline (schema inspection → normalization → chunking → ingest). Triggered by EventBridge Scheduler daily or manually via Fabric. |
| **CSV Pipeline — Lambda** | Python 3.11, `minelogx-<env>-csv` | Executes each Step Functions stage. Annotates data with Bedrock Claude, embeds with Cohere, ingests to OpenSearch `csv_telemetry_vecs`. |
| **PDF Pipeline — Lambda** | Python 3.11, `minelogx-<env>-pdf` | Event-driven (S3 PutObject → EventBridge Rule). Classifies PDFs (Haiku 4.5), extracts text (Textract + Sonnet 4.6), embeds (Titan), ingests to `pdf_legal_vecs`. |
| **Vector Store** | Amazon OpenSearch Serverless (VECTORSEARCH collection) | Stores and serves embeddings for both pipelines. Two indices: `csv_telemetry_vecs` (Cohere, 1024d) and `pdf_legal_vecs` (Titan, 1536d). Hybrid kNN + BM25. |
| **Object Storage** | Amazon S3 | Three buckets: telemetry data (CSV source), legislation documents (PDF source), CFN template uploads. See [Data Flow](data-flow.md) for the lifecycle prefix structure. |
| **AI Inference** | Amazon Bedrock | Claude Sonnet 4.6 (chat, analysis, CSV annotation, PDF extraction), Cohere Embed Multilingual v3 (CSV embeddings), Titan Embed Text v2 (PDF embeddings), Haiku 4.5 (PDF classification). |
| **Guardrails** | Amazon Bedrock Guardrails | `iot-mining-poc-guardrail-v1` — applied at every AI touchpoint. Blocks prompt injection, filters PII, denies off-topic requests. |
| **Scheduler** | Amazon EventBridge Scheduler | Triggers the CSV Step Functions state machine daily. Configurable cron expression. |
| **PDF Trigger** | Amazon EventBridge Rule | Listens for `s3:ObjectCreated:*` events on the legislation bucket with `.pdf` suffix → invokes PDF Lambda. |
| **Orchestration** | AWS Fabric (fabfile.py) | Developer-facing automation layer. Drives `env.up/down/plan`, pipeline invocations, frontend deploys, log tailing, health checks. Not a runtime container — runs locally. |

---

## Key Interaction Flows

### 1. Dashboard data request (GET endpoints, no AI)

```
Browser → Amplify CDN → API Gateway → API Lambda → S3 (curated/) → JSON response
```

Latency target: < 5 s. No Bedrock call is made for GET endpoints.

---

### 2. Chat / compliance Q&A (POST /chat)

```
Browser → Amplify CDN
  → API Gateway (HTTPS + CORS)
  → API Lambda
    → Bedrock Guardrails  (evaluate user query)
    → OpenSearch Serverless  (hybrid kNN + BM25 retrieval from pdf_legal_vecs)
    → Amazon Bedrock  (Claude Sonnet 4.6 — grounded generation)
    → Bedrock Guardrails  (evaluate response)
  → JSON response with citations
```

The API Gateway hard timeout is 29 seconds. For streaming responses in the future, the Lambda Function URL
is provisioned as a fallback path (see `apigw.yaml` TODO comment).

---

### 3. CSV telemetry ingestion (batch pipeline)

```
S3 telemetry-data bucket  (raw CSV files)
  → EventBridge Scheduler  (daily cron, or manual: fab lambda.invoke-all csv dev)
  → Step Functions  (4-stage state machine per file)
    → Stage 1: Schema Inspection  → API Lambda CSV
    → Stage 2: Normalization       → API Lambda CSV
    → Stage 3: Chunking            → API Lambda CSV + Bedrock Claude (annotation)
    → Stage 4: OpenSearch Ingest   → API Lambda CSV + Cohere Embed → csv_telemetry_vecs
```

---

### 4. PDF legal document ingestion (event-driven)

```
S3 legislation-documents bucket  (PDF upload)
  → EventBridge Rule  (S3 ObjectCreated *.pdf)
  → PDF Lambda
    → Bedrock Guardrails  (classify content)
    → Haiku 4.5  (document classification)
    → Amazon Textract  (text extraction for dense PDFs)
    → Bedrock Claude Sonnet 4.6  (section extraction for complex layouts)
    → Titan Embed Text v2  (embed sections)
    → OpenSearch Serverless  (ingest to pdf_legal_vecs)
```

---

## IaC Ownership

| Container | Provisioned by |
|---|---|
| Frontend (Amplify App + Branch) | CloudFormation `amplify/amplify.yaml` |
| API Gateway | CloudFormation `apigw/apigw.yaml` |
| All 3 Lambda functions + layers | CloudFormation `lambda/lambda.yaml` |
| Step Functions state machine | CloudFormation `step-functions/step-functions.yaml` |
| EventBridge Scheduler + Rule | CloudFormation `eventbridge/eventbridge.yaml` |
| OpenSearch Serverless collection | CloudFormation `opensearch-serverless/opensearch-serverless.yaml` |
| S3 buckets | CloudFormation `s3/s3.yaml` |
| IAM roles | CloudFormation `iam/iam.yaml` |
| Bedrock Guardrails | CloudFormation `bedrock-guardrails/bedrock-guardrails.yaml` |
| VPC, subnets, security groups | CloudFormation `network/` + `security-groups/` |

All stacks are composed by `parent.yaml` and deployed via `uv run fab env.up <env>`.
