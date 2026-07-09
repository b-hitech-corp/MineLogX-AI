# CLAUDE.md — MineLogX AI Platform

This file provides context and behavioral instructions for Claude Code agents working on the MineLogX AI Platform infrastructure repository. Read this file completely before making any changes.

---

## Project Overview

MineLogX AI is an operational intelligence platform for mining operations built on AWS. It combines IoT telemetry analytics, machine learning anomaly detection, and compliance Q&A (RAG) powered by Amazon Bedrock.

The infrastructure is defined primarily in **CloudFormation** (`onprem-aws/infrastructure/cloudformation/`). **Fabric is the orchestration layer**: it drives environment lifecycle (`--engine=terraform|cloudformation`) and handles remote operations on the demo EC2 instances.

> **Current status (2026-07-09):** Stack `minelogx-dev` is live (`CREATE_COMPLETE`) in `us-east-1`. HTTP API Gateway at `https://f81kmc7x2d.execute-api.us-east-1.amazonaws.com/dev`, 8 GET endpoints returning real data, frontend deployed to Amplify. CSV pipeline run (15/15 SUCCEEDED). PDF pipeline pending re-run. See `PLAN-ARCHITECTURE-GOAL.md` for full pending task list.

---

## Repository Structure

```
MineLogX-AI/                          # framework root
├── CLAUDE.md  README.md  CONTRIBUTING.md  AGENTS.md
├── fabfile.py                        # Fabric orchestrator (env.* + ollama.*), target-aware (MINELOGX_TARGET)
├── pyproject.toml  uv.lock  .python-version    # uv, Python >= 3.11
├── .pre-commit-config.yaml  .yamllint  .gitattributes
├── .github/workflows/lint.yml        # CI linters
├── docs/
├── shared/                           # cloud-agnostic core
│   ├── modules/ connectors/ templates/
│   └── frontend/                     # React app / AWS Amplify (cloud-agnostic UI)
├── onprem-aws/                       # AWS target — reference implementation
│   ├── infrastructure/
│   │   ├── terraform/                # State owner of the imported demo
│   │   │   ├── versions.tf  variables.tf  backend.tf
│   │   │   ├── modules/              # vpc, security_groups, s3, iam, lambda, api_gateway, ...
│   │   │   ├── environments/         # _imported-demo, dev/qa/prod, ephemeral
│   │   │   └── imports/              # import {} blocks for the demo
│   │   ├── cloudformation/           # Equivalent CFN definition for new envs
│   │   │   ├── network/ s3/ iam/ lambda/ apigw/ eventbridge/
│   │   │   ├── step-functions/ opensearch-serverless/ bedrock-guardrails/
│   │   │   └── params/
│   │   └── discovery/                # gitignored — output of discover-aws.*
│   ├── backend/                      # Lambda + Bedrock agent code
│   ├── scripts/                      # discover-aws.{sh,ps1}
│   └── (planned) pipelines/ connectors/ modules/ tests/
```

---

## AWS Architecture

### Core Components

| Layer | Service | Purpose |
|---|---|---|
| Frontend & API | AWS Amplify, API Gateway, Lambda | User interface and request routing |
| Data Analysis | Amazon Bedrock Claude | Real-time telemetry analysis and KPI calculation via Strands agent |
| RAG Agent | Amazon Bedrock Agent | Compliance Q&A with hybrid search |
| Vector Store | Amazon OpenSearch Serverless | Central vector store for both agents |
| CSV Pipeline | EventBridge Scheduler → Step Functions → Lambda → Bedrock Claude → Bedrock Cohere | Batch telemetry vectorization |
| PDF Pipeline | S3 PutObject → EventBridge → Lambda File Classification → Textract/Bedrock Claude → Lambda → Bedrock Titan | Event-driven legal doc vectorization |
| Storage | Amazon S3 | Raw data, curated data, vector inputs, logs |
| Remote Ops | Fabric + EC2 | Deployment automation for Ollama instances (demo only) |

### S3 Bucket Structure

All data flows through a lifecycle-controlled S3 bucket with strict prefix routing:

```
s3://iot-mining-poc/
├── raw/              # Untrusted incoming data — NEVER sent directly to Bedrock
├── quarantine/       # Failed validation or guardrail checks
├── approved/         # Validated data ready for processing
├── curated/          # Processed and annotated data
├── vector-input/     # Guardrail-passed chunks ready for embedding
└── logs/
    ├── guardrails/
    ├── validation/
    ├── embedding/
    └── opensearch-ingest/
```

**Critical rule**: Nothing from `raw/` goes directly to Bedrock embedding models or OpenSearch. All data must pass validation and Bedrock Guardrail checks before reaching `vector-input/`.

### OpenSearch Serverless Indices

```
Collection: minelogx-vector-store (OpenSearch Serverless - Vector search type)
├── csv_telemetry_vecs   # Cohere embeddings — 1024 dimensions — telemetry data
└── pdf_legal_vecs       # Titan embeddings — 1536 dimensions — legal documents
```

Both indices use HNSW with Faiss engine for kNN vector search, plus BM25 for hybrid search.

### Bedrock Models

| Model ID | Use Case | Pipeline |
|---|---|---|
| `us.anthropic.claude-sonnet-4-6` | Data analysis, CSV annotation, complex PDF extraction, RAG Q&A | API Lambda, CSV Pipeline, PDF Pipeline |
| `us.amazon.nova-pro-v1:0` | RAG Compliance Q&A (selectable) | RAG Agent |
| `deepseek.v3.2` | RAG Compliance Q&A (selectable) | RAG Agent |
| `cohere.embed-multilingual-v3` | Telemetry vectorization (1024d) | CSV Pipeline |
| `amazon.titan-embed-text-v2:0` | Legal document vectorization (1024d) | PDF Pipeline |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | PDF document classifier (Signal 3) | PDF Pipeline — GRANTED in this account |

All models use cross-region inference profiles (prefix `us.` for Claude/Nova). Bare model IDs like `anthropic.claude-3-5-sonnet-20241022-v2:0` raise `ResourceNotFoundException` in this account.

### Bedrock Guardrails

A single reusable guardrail named `iot-mining-poc-guardrail-v1` must be applied at all AI touchpoints:
- User queries before Bedrock Agent execution
- PDF chunks before embedding
- CSV/telemetry chunks before embedding
- Final Bedrock Agent responses before returning to the user

Guardrail must detect/block: prompt injection, system prompt extraction, access control bypass, hidden tool calls, OpenSearch/S3 modification attempts, and ingestion pipeline triggers. Also filter PII: emails, phone numbers, addresses, employee IDs, contract IDs, site IDs.

### EC2 Ollama Instances (demo only)

These will be replaced by Bedrock in production. Managed via Fabric.

| Instance | Model | Endpoint |
|---|---|---|
| minelogx-qwen3 | Qwen3 8B | ec2-98-81-228-187.compute-1.amazonaws.com:11434 |
| minelogx-gemma3 | Gemma3 12B | ec2-100-31-82-64.compute-1.amazonaws.com:11434 |
| minelogx-embeddings | mxbai-embed-large | ec2-3-208-23-94.compute-1.amazonaws.com:11434 |

---

## Fabric — Remote Operations

Fabric (with Invoke) is the automation entrypoint for the project. It has six task namespaces:
- **`env.*`** — infrastructure environment lifecycle, running Terraform **or** CloudFormation (`--engine`).
- **`lambda.*`** — invoke pipelines, set env vars, view logs, check status, build layers.
- **`bedrock.*`** — probe model access across all project models.
- **`opensearch.*`** — collection health and document count per index.
- **`frontend.*`** — build and deploy the React/Vite app to Amplify.
- **`ollama.*`** — remote SSH operations on the demo EC2 instances running Ollama.

### Installation

This project uses **uv** as the package manager and requires **Python >= 3.11**
(pinned in `.python-version`, declared in `pyproject.toml`).

```bash
uv sync          # create .venv and install deps (fabric) from pyproject/uv.lock
uv run fab --list
```

### fabfile.py Structure

The fabfile uses `invoke.Collection` namespaces so tasks are grouped as `<ns>.<task>`:

```
env_ns      → env.*        (up, plan, down, list, bootstrap, endpoints)
lambda_ns   → lambda.*     (invoke, invoke-all, set-env, logs, status, build-layer, pull, pdf-async-status)
bedrock_ns  → bedrock.*    (model-access)
opensearch_ns → opensearch.* (status)
frontend_ns → frontend.*   (deploy)
ollama_ns   → ollama.*     (health-check, restart-ollama, pull-model, logs)
```

Activity logs are written to `.fab-logs/` (git-ignored):
`invoke-csv-<env>-<ts>.log`, `invoke-pdf-<env>-<ts>.log`, `opensearch-status-<env>-<ts>.log`

### Common Fabric Tasks

```bash
# --- Environment orchestration (env.*) ---
uv run fab env.up   dev --seed                        # deploy + seed S3 from demo buckets
uv run fab env.up   dev-cesar --engine=terraform      # ephemeral per-dev env
uv run fab env.plan dev                               # preview changes (CFN change set)
uv run fab env.down dev-cesar                         # tear down (prod is guarded)
uv run fab env.list                                   # active workspaces + stacks
uv run fab env.endpoints                              # print live URLs (default: dev)
uv run fab env.endpoints qa                           # same for another env

# --- Lambda pipeline ops (lambda.*) ---
uv run fab lambda.invoke csv dev --wait               # trigger CSV Step Functions pipeline
uv run fab lambda.invoke pdf dev                      # invoke PDF Lambda with S3 synthetic event
uv run fab lambda.invoke pdf dev --async              # fire-and-forget (InvocationType=Event)
uv run fab lambda.invoke-all csv dev --parallel       # process all S3 CSVs in parallel
uv run fab lambda.invoke-all pdf dev --async          # queue all PDFs asynchronously
uv run fab lambda.pdf-async-status                    # CloudWatch Logs Insights: per-PDF status table (default: dev)
uv run fab lambda.redeploy api dev                    # re-zip backend/ + update-function-code (no layer rebuild)
uv run fab lambda.set-env pdf dev --key PDF_HAIKU_MODEL_ID --value us.anthropic.claude-haiku-4-5-20251001-v1:0
uv run fab lambda.logs api dev --follow               # tail CloudWatch logs
uv run fab lambda.status                              # state + env vars for api/csv/pdf (default: dev)

# --- Bedrock model probing (bedrock.*) ---
uv run fab bedrock.model-access                       # probe all project models (GRANTED/DENIED)

# --- OpenSearch status (opensearch.*) ---
uv run fab opensearch.status                          # collection health + doc counts (default: dev)

# --- Frontend (frontend.*) ---
uv run fab frontend.deploy dev                        # build React/Vite + push to Amplify (standalone)
uv run fab env.up dev                                 # full-stack: infra + frontend en un solo comando
uv run fab env.up dev --skip-frontend                 # solo infra, sin rebuild del frontend
# VITE_API_BASE_URL is injected dynamically from the stack outputs (never hardcoded)

# --- Ollama demo remote ops (ollama.*) ---
uv run fab ollama.health-check                        # check all instances
uv run fab ollama.pull-model --host=qwen3 --model=qwen3:8b
uv run fab ollama.restart-ollama
uv run fab ollama.logs --host=gemma3
```

### Example Fabric Task Pattern

```python
@task
def health_check(c):
    """Check all Ollama instances are responding"""
    for name, host in INSTANCES.items():
        conn = Connection(host, user=USER,
                         connect_kwargs={"key_filename": KEY_PATH})
        result = conn.run(
            "curl -s http://localhost:11434/api/tags",
            hide=True
        )
        print(f"{name}: {'OK' if result.ok else 'FAILED'}")

@task
def restart_ollama(c):
    """Restart Ollama Docker container on all instances"""
    group = SerialGroup(
        *INSTANCES.values(),
        user=USER,
        connect_kwargs={"key_filename": KEY_PATH}
    )
    group.run("docker restart ollama")
```

---

## IaC Strategy

**Dual-tool, Fabric-orchestrated.** The platform is defined **in full in both
Terraform and CloudFormation**. The two definitions are kept at parity; Fabric
selects the engine per environment via `--engine`.

### Ownership rule (non-negotiable)
A live AWS resource can be managed by only **one** engine at a time — never both,
or they fight over drift/deletion. Therefore:
- **Terraform is the state owner of the imported demo** (`environments/_imported-demo`).
  It is the source of truth for what is already deployed.
- **CloudFormation holds an equivalent, deployable definition** used to stand up
  **new** environments (ephemeral / dev / qa). It does **not** co-manage the
  demo's live resources.
- When adding or changing infrastructure, update **both** definitions and keep
  them at parity (verified in CI).

### Layers provisioned (target architecture)
Both engines (`modules/env_stack` ↔ `cloudformation/parent.yaml`) provision every
service in `docs/architecture/`: network, security groups, S3 (with EventBridge
notifications on the legislation bucket), IAM, CloudWatch, API Gateway, Amplify,
EC2 Ollama fallback, **OpenSearch Serverless** (VECTORSEARCH collection),
**Bedrock Guardrail**, three **Lambdas** (`api` / `csv` / `pdf`), the CSV
**Step Functions** state machine, and **EventBridge** (a Scheduler firing the CSV
pipeline + a Rule routing PDF uploads to the PDF Lambda). The AOSS vector indices
(`csv_telemetry_vecs`, `pdf_legal_vecs`) are data-plane objects the ingest Lambdas
create via the OpenSearch API, not IaC. Lambda **runtime deps** (pandas, pdfplumber,
opensearch-py, …) are supplied via a layer/container built separately — the IaC
zips ship code only.

### Environments (both models)
- **Ephemeral per-developer:** `dev-<user>` (e.g. `dev-cesar`), created/destroyed
  on demand. Terraform isolates them with a **workspace**; CloudFormation with a
  stack-name prefix `minelogx-dev-<user>-<layer>`.
- **Fixed shared:** `dev`, `qa`, `prod` — each a dedicated Terraform root
  module under `environments/` and its own CFN parameter file.

All environments are driven through Fabric (`fab env.up/plan/down/list`), never
by hand in the console.

### Discovery → Import workflow (demo capture)
1. Configure a **dedicated AWS profile** (`aws configure --profile minelogx` or
   `aws configure sso --profile minelogx`); scope it per shell with
   `AWS_PROFILE=minelogx` so other projects are unaffected.
2. Run `scripts/discover-aws.sh` (or `.ps1`) → snapshots the account (filtered by
   the `aws-apn-id` tag) into `infrastructure/discovery/` (gitignored).
3. Write `import {}` blocks in `infrastructure/terraform/imports/`, then
   `terraform plan -generate-config-out=generated.tf`, refactor into `modules/`,
   `apply` (imports only), and confirm `plan` shows **0 changes**.
4. Author the equivalent CloudFormation templates per layer.

### Tagging
Every resource carries `aws-apn-id = pc:13uw3s8iyvze74tlcq3o0w8r6`, `Environment`,
and `ManagedBy` (`terraform` | `cloudformation`). Terraform applies these via
`default_tags`; Fabric passes them to `cloudformation deploy --tags`.

### Applying Changes

**Terraform:**
```bash
# Always plan before apply — never apply without reviewing plan
terraform init                          # First time or after provider changes
terraform fmt -recursive                # Format before committing
terraform validate                      # Validate syntax
terraform plan -out=tfplan              # Plan changes
terraform apply tfplan                  # Apply only after reviewing plan

# Destroy (use with extreme caution)
terraform plan -destroy -out=tfplan-destroy
terraform apply tfplan-destroy
```

**CloudFormation:**
```bash
# Validate before deploying
aws cloudformation validate-template \
  --template-body file://infrastructure/cloudformation/<template>.yaml

# Deploy or update
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/<template>.yaml \
  --stack-name minelogx-<resource> \
  --capabilities CAPABILITY_IAM \
  --region us-east-1

# Monitor deployment
aws cloudformation describe-stack-events \
  --stack-name minelogx-<resource>

# Check outputs
aws cloudformation describe-stacks \
  --stack-name minelogx-<resource>
```

---

## Git Workflow — Git Flow

### Branch Strategy

```
main          # Production-ready code only — protected, requires PR + approval
develop       # Integration branch — all features merge here first
release/*     # Release candidates — e.g. release/1.2.0
hotfix/*      # Emergency fixes branched from main
feature/*     # All new work — branched from develop
```

### Branch Naming Convention

```
feature/BHMIB-{ticket-number}-short-description

Examples:
feature/BHMIB-57-opensearch-serverless-module
feature/BHMIB-82-csv-vectorization-pipeline
feature/BHMIB-91-bedrock-guardrails-cfn
feature/BHMIB-103-fabric-ec2-automation
hotfix/BHMIB-99-fix-lambda-timeout
release/1.2.0
```

Rules:
- Always branch from `develop` for features
- Always branch from `main` for hotfixes
- Lowercase and hyphens only — no spaces or underscores
- Description: 3-5 words max

### Commit Message Format

```
[BHMIB-{ticket}] {type}: {short description}

Types:
  feat     — new feature or capability
  fix      — bug fix
  chore    — maintenance, dependency updates, config changes
  refactor — code restructure without behavior change
  docs     — documentation only
  test     — adding or updating tests
  infra    — Terraform or CloudFormation changes
  ci       — CI/CD pipeline changes
  fab      — Fabric task additions or changes
```

**Examples:**
```
[BHMIB-57] feat: add OpenSearch Serverless Terraform module
[BHMIB-57] infra: configure knn_vector index mappings for Cohere embeddings
[BHMIB-82] feat: implement CSV vectorization Step Functions state machine
[BHMIB-82] fix: correct S3 prefix routing from raw to approved
[BHMIB-91] infra: add Bedrock guardrail CloudFormation template
[BHMIB-103] fab: add health-check and restart-ollama tasks
[BHMIB-103] fab: add deploy-lambda task for automated function updates
```

Rules:
- Subject line max 72 characters
- Use imperative mood — "add", "fix", "update" not "added", "fixed", "updated"
- Reference the Jira ticket in every commit
- One logical change per commit

### Pull Request Process

1. Branch from `develop` (or `main` for hotfixes)
2. Make atomic commits following the format above
3. Push and open PR against `develop`
4. PR title must follow commit format: `[BHMIB-57] feat: add OpenSearch module`
5. PR description must include:
   - What changed and why
   - How to test/verify
   - Any manual steps required for infrastructure changes
   - Fabric tasks affected if any
6. Squash merge into `develop` after approval

---

## Environment Variables & Secrets

**Never hardcode** these values — use environment variables, AWS SSM Parameter Store, or Secrets Manager:

```
AWS_REGION
OPENSEARCH_ENDPOINT
OPENSEARCH_CSV_INDEX            # csv_telemetry_vecs
OPENSEARCH_PDF_INDEX            # pdf_legal_vecs
BEDROCK_GUARDRAIL_ID
BEDROCK_GUARDRAIL_VERSION
S3_BUCKET_NAME                  # iot-mining-poc
QWEN3_ENDPOINT                  # demo only
GEMMA3_ENDPOINT                 # demo only
EMBEDDINGS_ENDPOINT             # demo only
EC2_KEY_PATH                    # Path to .pem file for Fabric
```

For Terraform, use `terraform.tfvars` (gitignored) or AWS SSM Parameter Store.
For Fabric, use environment variables or a `.env` file (gitignored).

---

## Lambda Function Guidelines

- Runtime: Python 3.11
- Timeout: 5 minutes minimum for AI-heavy functions
- Memory: 256MB default, 512MB+ for PDF processing
- All Lambdas must log structured JSON to CloudWatch
- S3 prefix routing must be strictly enforced — never read from `raw/` for Bedrock
- Use Lambda Function URLs for LLM calls (API Gateway 29s timeout limitation)
- Data endpoints (GET /fleet/assets, /kpis, /fuel/*, /maintenance/*, /telemetry/*) van por HTTP API v2 — respuesta < 5s sin LLM
- Chat y Company usan Lambda Function URL directa (streaming futuro; ver TODO en apigw.yaml)

```python
# Required structured logging pattern
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    logger.info(json.dumps({
        "event": "lambda_invoked",
        "function": context.function_name,
        "request_id": context.aws_request_id
    }))
```

---

## Critical Architecture Constraints

1. **API Gateway timeout = 29s hard limit** — LLM calls (chat, analyze) usan Lambda Function URL; los GET de datos van por HTTP API v2 (< 5s)
2. **OpenSearch uses hybrid search** — kNN (vector) + BM25 (lexical) for RAG queries
3. **Raw data is untrusted** — validate before any Bedrock operation
4. **Bedrock Guardrails are mandatory** at all AI touchpoints — never bypass
5. **Multi-tenant isolation** is a Phase 2 requirement — design IAM and S3 policies with tenant separation in mind
6. **EC2 Ollama instances are demo-only** — will be replaced by Bedrock in production
7. **Terraform remote state** must use S3 + DynamoDB — never local state
8. **CloudFormation stacks** prefixed with `minelogx-` for easy identification
9. **Fabric tasks** should be idempotent — safe to run multiple times
10. **Never apply infrastructure changes directly from console** in non-dev environments — IaC only

---

## Terraform Remote State

```hcl
terraform {
  backend "s3" {
    bucket         = "minelogx-poc-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "minelogx-terraform-locks"
    encrypt        = true
  }
}
```

---

## Useful Commands Reference

```bash
# Terraform
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
terraform output

# CloudFormation
aws cloudformation deploy ...
aws cloudformation validate-template --template-body file://...
aws cloudformation describe-stack-events --stack-name minelogx-X

# Fabric
fab --list                                    # List all available tasks
fab env.up --env=dev-cesar --engine=terraform # Stand up an ephemeral env
fab env.list                                  # List active environments
fab ollama.health-check                       # Check all EC2 instances
fab ollama.restart-ollama                     # Restart Ollama on all instances

# AWS CLI helpers
aws opensearch list-domain-names
aws bedrock list-guardrails
aws lambda list-functions
aws s3 ls s3://iot-mining-poc/
aws s3 ls s3://iot-mining-poc/raw/
aws s3 ls s3://iot-mining-poc/vector-input/
```

---

## When in Doubt

- Review the architecture diagram in `/docs/architecture/` for reference
- All non-dev infrastructure changes must go through IaC — no manual console changes
- Terraform vs CloudFormation: follow the IaC Strategy section above
- Fabric vs manual SSH: always prefer Fabric for repeatable EC2 operations
- If a Terraform resource type is unavailable, use CloudFormation and document it here
- If unsure about S3 prefix routing, re-read the S3 Bucket Structure section — raw data never touches Bedrock directly
