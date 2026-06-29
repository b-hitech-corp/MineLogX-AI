# CLAUDE.md — MineLogX AI Platform

This file provides context and behavioral instructions for Claude Code agents working on the MineLogX AI Platform infrastructure repository.

---

## Project Overview

MineLogX AI is an operational intelligence platform for mining operations built on AWS. It combines IoT telemetry analytics, machine learning anomaly detection, and compliance Q&A (RAG) powered by Amazon Bedrock.

The infrastructure is defined as IaC using both Terraform and AWS CloudFormation depending on the resource type (see IaC Strategy below).

---

## Repository Structure

```
minelogx-platform/
├── CLAUDE.md                        # This file
├── infrastructure/
│   ├── terraform/                   # Terraform modules
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── terraform.tfvars
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
| Data Analysis | Amazon Bedrock Claude | Real-time telemetry analysis and KPI calculation |
| RAG Agent | Amazon Bedrock Agent | Compliance Q&A with hybrid search |
| Vector Store | Amazon OpenSearch Serverless | Central vector store for both agents |
| CSV Pipeline | EventBridge Scheduler → Step Functions → Lambda → Bedrock Claude → Bedrock Cohere | Batch telemetry vectorization |
| PDF Pipeline | S3 PutObject → EventBridge → Lambda → Textract/Bedrock Claude → Lambda → Bedrock Titan | Event-driven legal doc vectorization |
| Storage | Amazon S3 | Raw data, curated data, vector inputs, logs |

### S3 Bucket Structure

All data flows through a lifecycle-controlled S3 bucket:

```
s3://iot-mining-poc/
├── raw/              # Untrusted incoming data — never sent directly to Bedrock
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

**Critical rule**: Nothing from `raw/` goes directly to Bedrock embedding models or OpenSearch. All data must pass validation and guardrail checks before reaching `vector-input/`.

### OpenSearch Indices

```
minelogx-vector-store (OpenSearch Serverless)
├── csv_telemetry_vecs   # Cohere embeddings (1024 dimensions) — telemetry data
└── pdf_legal_vecs       # Titan embeddings (1536 dimensions) — legal documents
```

### Bedrock Models

| Model | Use Case | Pipeline |
|---|---|---|
| Claude (claude-3-5-sonnet) | Data analysis agent, CSV annotation, PDF complex extraction | Data Analysis Layer, CSV Pipeline, PDF Pipeline |
| Cohere (embed-multilingual-v3) | Telemetry vectorization | CSV Pipeline |
| Titan (amazon.titan-embed-text-v2:0) | Legal document vectorization | PDF Pipeline |

### Bedrock Guardrails

A single reusable guardrail `iot-mining-poc-guardrail-v1` must be applied at:
- User queries before Bedrock Agent execution
- PDF chunks before embedding
- CSV/telemetry chunks before embedding
- Final Bedrock Agent responses before returning to user

---

## IaC Strategy

### Use Terraform for:
- VPC, subnets, security groups, route tables
- S3 buckets and lifecycle policies
- IAM roles and policies
- Lambda functions
- API Gateway
- EventBridge rules and schedulers
- CloudWatch log groups
- Network resources (NAT Gateway, Internet Gateway, VPC endpoints)

### Use CloudFormation for:
- Amazon Bedrock Guardrails (not yet supported in Terraform AWS provider)
- Amazon OpenSearch Serverless collections and policies (better native support)
- AWS Step Functions state machines (complex JSON/YAML definitions easier in CFN)
- Any resource where the AWS CloudFormation resource type is more stable or complete

### Applying Changes

**Terraform:**
```bash
# Always plan before apply
terraform plan -out=tfplan

# Review plan output carefully before applying
terraform apply tfplan

# For destroying resources
terraform plan -destroy -out=tfplan-destroy
terraform apply tfplan-destroy
```

**CloudFormation:**
```bash
# Deploy or update a stack
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/<template>.yaml \
  --stack-name minelogx-<resource> \
  --capabilities CAPABILITY_IAM \
  --region us-east-1

# Validate template before deploying
aws cloudformation validate-template \
  --template-body file://infrastructure/cloudformation/<template>.yaml

# Check stack status
aws cloudformation describe-stacks \
  --stack-name minelogx-<resource>
```

---

## Git Workflow — Git Flow

### Branch Strategy

```
main          # Production-ready code only — protected branch
develop       # Integration branch — all features merge here first
release/*     # Release candidates (e.g. release/1.2.0)
hotfix/*      # Emergency fixes directly from main
feature/*     # All new work
```

### Branch Naming Convention

```
feature/BHMIB-{ticket-number}-short-description

Examples:
feature/BHMIB-57-opensearch-serverless-module
feature/BHMIB-82-csv-vectorization-pipeline
feature/BHMIB-91-bedrock-guardrails-cfn
hotfix/BHMIB-99-fix-lambda-timeout
release/1.2.0
```

Rules:
- Always branch from `develop` for features
- Always branch from `main` for hotfixes
- Use lowercase and hyphens only — no spaces or underscores
- Keep descriptions short (3-5 words max)

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
  infra    — infrastructure and IaC changes
  ci       — CI/CD pipeline changes
```

**Examples:**
```
[BHMIB-57] feat: add OpenSearch Serverless Terraform module
[BHMIB-57] infra: configure knn_vector index mappings for Cohere embeddings
[BHMIB-82] feat: implement CSV vectorization Step Functions state machine
[BHMIB-82] fix: correct S3 prefix routing from raw to approved
[BHMIB-91] infra: add Bedrock guardrail CloudFormation template
[BHMIB-91] chore: update IAM policies for guardrail access
```

Rules:
- Subject line max 72 characters
- Use imperative mood ("add", "fix", "update" — not "added", "fixed", "updated")
- Reference the Jira ticket in every commit
- One logical change per commit — avoid bundling unrelated changes

### Pull Request Process

1. Create feature branch from `develop`
2. Make changes with atomic commits following the format above
3. Push branch and open PR against `develop`
4. PR title must follow commit format: `[BHMIB-57] feat: add OpenSearch module`
5. PR description must include:
   - What changed and why
   - How to test / verify
   - Any infrastructure changes that require manual steps
6. Squash merge into `develop` after approval

---

## Environment Variables & Secrets

**Never hardcode** the following — always use environment variables or AWS Secrets Manager:

```
AWS_REGION
OPENSEARCH_ENDPOINT
OPENSEARCH_CSV_INDEX
OPENSEARCH_PDF_INDEX
BEDROCK_GUARDRAIL_ID
BEDROCK_GUARDRAIL_VERSION
S3_BUCKET_NAME
QWEN3_ENDPOINT        # EC2 Ollama (POC only)
GEMMA3_ENDPOINT       # EC2 Ollama (POC only)
EMBEDDINGS_ENDPOINT   # EC2 Ollama (POC only)
```

For Terraform, use `terraform.tfvars` (never committed) or AWS SSM Parameter Store.

---

## Lambda Function Guidelines

- Runtime: Python 3.11
- Timeout: 5 minutes minimum for AI-heavy functions (ML layer, RAG layer)
- Memory: 256MB default, 512MB+ for PDF processing
- All Lambdas must log to CloudWatch with structured JSON logging
- All Lambdas must handle S3 prefix routing correctly (raw → approved → vector-input)
- Never read directly from `raw/` prefix for Bedrock operations

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

## Important Constraints & Decisions

1. **API Gateway timeout is 29 seconds** — for LLM calls use Lambda Function URLs directly
2. **OpenSearch uses hybrid search** — both vector (kNN) and lexical (BM25) for RAG queries
3. **Raw data is untrusted** — always validate before processing with Bedrock
4. **Bedrock Guardrails are mandatory** at all AI touchpoints — never bypass
5. **Multi-tenant isolation** is a future requirement — design IAM and S3 policies with tenant separation in mind even for POC
6. **EC2 Ollama instances** (Qwen3, Gemma3, mxbai) are POC-only — will be replaced by Bedrock in production
7. **Terraform state** must be stored remotely in S3 with DynamoDB locking — never use local state
8. **CloudFormation stacks** should be prefixed with `minelogx-` for easy identification

---

## Terraform Remote State Setup

Before running any Terraform commands, ensure remote state is configured:

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
terraform init                          # Initialize providers and backend
terraform fmt -recursive                # Format all .tf files
terraform validate                      # Validate configuration
terraform plan -out=tfplan              # Plan changes
terraform apply tfplan                  # Apply planned changes
terraform output                        # Show output values

# CloudFormation
aws cloudformation deploy ...           # Deploy/update stack
aws cloudformation validate-template   # Validate before deploy
aws cloudformation describe-stack-events --stack-name minelogx-X  # Debug failures

# AWS CLI helpers
aws opensearch list-domain-names        # List OpenSearch domains
aws bedrock list-guardrails             # List Bedrock guardrails
aws lambda list-functions               # List Lambda functions
aws s3 ls s3://iot-mining-poc/          # Inspect S3 bucket structure
```

---

## When in Doubt

- Check the architecture diagram in `/docs/architecture/` for reference
- All infrastructure changes must be applied via IaC — never manual console changes in non-dev environments
- When adding a new AWS resource, decide Terraform vs CloudFormation using the IaC Strategy section above
- If a Terraform resource type is not yet available for a new AWS service, use CloudFormation and document it here