# CLAUDE.md — MineLogX AI Platform

This file provides context and behavioral instructions for Claude Code agents working on the MineLogX AI Platform infrastructure repository. Read this file completely before making any changes.

---

## Project Overview

MineLogX AI is an operational intelligence platform for mining operations built on AWS. It combines IoT telemetry analytics, machine learning anomaly detection, and compliance Q&A (RAG) powered by Amazon Bedrock.

The infrastructure is defined as IaC using both Terraform and AWS CloudFormation depending on the resource type (see IaC Strategy below). Fabric is used for remote operations and deployment automation on EC2 instances.

---

## Repository Structure

```
minelogx-platform/
├── CLAUDE.md                        # This file
├── fabfile.py                       # Fabric tasks for remote operations
├── infrastructure/
│   ├── terraform/                   # Terraform modules
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── terraform.tfvars         # Never commit — contains secrets
│   │   └── modules/
│   │       ├── vpc/
│   │       ├── security_groups/
│   │       ├── s3/
│   │       ├── opensearch/
│   │       ├── lambda/
│   │       ├── api_gateway/
│   │       ├── eventbridge/
│   │       ├── step_functions/
│   │       └── iam/
│   └── cloudformation/              # CloudFormation templates
│       ├── bedrock-guardrails.yaml
│       ├── opensearch-serverless.yaml
│       ├── step-functions-csv.yaml
│       └── step-functions-pdf.yaml
├── backend/
│   ├── lambdas/
│   │   ├── ml-layer/                # ML analysis Lambda
│   │   ├── rag-layer/               # RAG compliance Lambda
│   │   ├── schema-inspector/        # CSV schema inspector
│   │   ├── chunker/                 # CSV chunk processor
│   │   ├── pdf-processor/           # PDF text processor
│   │   └── file-classification/     # PDF file classifier
│   └── agents/
│       ├── data-analysis/           # Bedrock Claude data analysis agent
│       └── rag-agent/               # Bedrock RAG compliance agent
└── frontend/                        # React application (AWS Amplify)
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
| Remote Ops | Fabric + EC2 | Deployment automation for Ollama instances (POC only) |

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

| Model | Use Case | Pipeline |
|---|---|---|
| claude-3-5-sonnet | Data analysis agent, CSV annotation, complex PDF extraction | Data Analysis Layer, CSV Pipeline, PDF Pipeline |
| cohere.embed-multilingual-v3 | Telemetry vectorization | CSV Pipeline |
| amazon.titan-embed-text-v2:0 | Legal document vectorization | PDF Pipeline |

### Bedrock Guardrails

A single reusable guardrail named `iot-mining-poc-guardrail-v1` must be applied at all AI touchpoints:
- User queries before Bedrock Agent execution
- PDF chunks before embedding
- CSV/telemetry chunks before embedding
- Final Bedrock Agent responses before returning to the user

Guardrail must detect/block: prompt injection, system prompt extraction, access control bypass, hidden tool calls, OpenSearch/S3 modification attempts, and ingestion pipeline triggers. Also filter PII: emails, phone numbers, addresses, employee IDs, contract IDs, site IDs.

### EC2 Ollama Instances (POC only)

These will be replaced by Bedrock in production. Managed via Fabric.

| Instance | Model | Endpoint |
|---|---|---|
| minelogx-qwen3 | Qwen3 8B | ec2-98-81-228-187.compute-1.amazonaws.com:11434 |
| minelogx-gemma3 | Gemma3 12B | ec2-100-31-82-64.compute-1.amazonaws.com:11434 |
| minelogx-embeddings | mxbai-embed-large | ec2-3-208-23-94.compute-1.amazonaws.com:11434 |

---

## Fabric — Remote Operations

Fabric is a high-level Python library for executing shell commands remotely over SSH, built on top of Invoke and Paramiko. It is used in this project for automating operations on EC2 instances running Ollama models.

### Installation

```bash
pip install fabric
```

### fabfile.py Structure

```python
from fabric import Connection, SerialGroup, task
from invoke import Collection

# EC2 instance connections
INSTANCES = {
    "qwen3":      "ec2-98-81-228-187.compute-1.amazonaws.com",
    "gemma3":     "ec2-100-31-82-64.compute-1.amazonaws.com",
    "embeddings": "ec2-3-208-23-94.compute-1.amazonaws.com",
}
KEY_PATH = "~/.ssh/minelogx-demo-poc-keypair.pem"
USER = "ubuntu"
```

### Common Fabric Tasks

```bash
# Check all Ollama instances are running
fab health-check

# Pull a new model on a specific instance
fab pull-model --host=qwen3 --model=qwen3:8b

# Restart Ollama container on all instances
fab restart-ollama

# Deploy updated Lambda code
fab deploy-lambda --function=ml-layer

# Check logs on a specific instance
fab logs --host=gemma3
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

### Use Terraform for:
- VPC, subnets, security groups, route tables, NAT/Internet Gateway
- S3 buckets and lifecycle policies
- IAM roles and policies
- Lambda functions and Function URLs
- API Gateway (REST and HTTP)
- EventBridge rules and schedulers
- CloudWatch log groups and alarms
- EC2 instances (Ollama — POC only)

### Use CloudFormation for:
- Amazon Bedrock Guardrails (not yet fully supported in Terraform AWS provider)
- Amazon OpenSearch Serverless collections and access policies
- AWS Step Functions state machines (complex ASL definitions easier in CFN)
- Any resource where the Terraform resource type is unstable or incomplete

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
QWEN3_ENDPOINT                  # POC only
GEMMA3_ENDPOINT                 # POC only
EMBEDDINGS_ENDPOINT             # POC only
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

1. **API Gateway timeout = 29s hard limit** — use Lambda Function URLs for LLM calls
2. **OpenSearch uses hybrid search** — kNN (vector) + BM25 (lexical) for RAG queries
3. **Raw data is untrusted** — validate before any Bedrock operation
4. **Bedrock Guardrails are mandatory** at all AI touchpoints — never bypass
5. **Multi-tenant isolation** is a Phase 2 requirement — design IAM and S3 policies with tenant separation in mind
6. **EC2 Ollama instances are POC-only** — will be replaced by Bedrock in production
7. **Terraform remote state** must use S3 + DynamoDB — never local state
8. **CloudFormation stacks** prefixed with `minelogx-` for easy identification
9. **Fabric tasks** should be idempotent — safe to run multiple times
10. **Never apply infrastructure changes directly from console** in non-dev environments — IaC only

---

## Terraform Remote State

```hcl
terraform {
  backend "s3" {
    bucket         = "minelogx-terraform-state"
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
fab --list                              # List all available tasks
fab health-check                        # Check all EC2 instances
fab restart-ollama                      # Restart Ollama on all instances
fab deploy-lambda --function=ml-layer   # Deploy Lambda function

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