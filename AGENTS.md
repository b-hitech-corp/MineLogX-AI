# AGENTS.md — MineLogX AI Platform

This file defines the AI agents present in the MineLogX platform, their roles, behavioral rules, available tools, and constraints. It applies to all AI agents operating in this repository — both development-time agents (Claude Code, GitHub Copilot, Cursor) and runtime agents (Amazon Bedrock Agents).

Read this file completely before taking any action.

---

## Agent Inventory

### 1. Development Agents (Code Generation & IaC)

| Agent | Tool | Scope |
|---|---|---|
| Claude Code | Anthropic Claude | Primary dev agent — IaC, Lambda, Fabric, architecture |
| GitHub Copilot | OpenAI | Inline code suggestions only |
| Cursor | Anthropic/OpenAI | Secondary dev agent if needed |

### 2. Runtime Agents (Platform AI)

| Agent | Service | Purpose |
|---|---|---|
| Data Analysis Agent | Amazon Bedrock Claude (Converse API) | KPI calculation, anomaly detection, telemetry analysis |
| RAG Compliance Agent | Amazon Bedrock Claude (Converse API + OpenSearch hybrid search) | Compliance Q&A with selectable model (Sonnet / Nova Pro / DeepSeek) |

---

## Rules for Development Agents

### What agents MAY do

- Read any file in the repository
- Create new files following the repository structure defined in `CLAUDE.md`
- Modify existing Terraform modules, CloudFormation templates, Lambda functions, and Fabric tasks
- Run `terraform fmt`, `terraform validate`, and `terraform plan`
- Run `aws cloudformation validate-template`
- Run `fab --list` and read-only Fabric tasks
- Run `fab docs.build` to preview the documentation site locally (`mkdocs serve`)
- Run tests and linters
- Suggest git commits following the `[BHMIB-{ticket}] {type}: {description}` format
- Install Python dependencies in a virtual environment

### What agents MUST NOT do

- Run `terraform apply` or `terraform destroy` without explicit human confirmation
- Run `aws cloudformation deploy` without explicit human confirmation
- Run any Fabric task that modifies EC2 state (restart, deploy) without explicit human confirmation
- Delete, overwrite, or modify `terraform.tfstate` files
- Commit or expose any value from the secrets list in `CLAUDE.md`
- Read from or write to `s3://iot-mining-poc/raw/` for any Bedrock operation
- Modify IAM policies to grant broader permissions than currently defined
- Access production AWS resources directly — dev environment only
- Push directly to `main` or `develop` branches — always use feature branches
- Generate or suggest hardcoded AWS credentials, API keys, or secrets in any file

### IaC rule (CloudFormation primary)

Infrastructure is defined in **CloudFormation** (`onprem-aws/infrastructure/cloudformation/`),
orchestrated by Fabric (`fab env.*`). See `CLAUDE.md` → *IaC Strategy* for full rules. Agents must:

- Create/destroy environments **only through Fabric tasks** — never with raw console clicks.
  Fixed envs: `dev`/`qa`/`prod`. Ephemeral: `dev-<user>`.
- Use `fab env.health <env>` after any deploy for a full check (lambda states, AOSS doc counts, Step Functions history, Bedrock access). Use `fab env.endpoints <env>` for a quick URL summary only.
- Populate `infrastructure/discovery/` only via `scripts/discover-aws.sh` (read only) and never commit it.

### File modification boundaries

```
✅ Agents may freely modify:
onprem-aws/infrastructure/terraform/modules/**
onprem-aws/infrastructure/terraform/environments/{dev,qa,ephemeral}/**
onprem-aws/infrastructure/cloudformation/**
onprem-aws/backend/lambdas/**
onprem-aws/backend/agents/**
onprem-aws/scripts/**
shared/**
fabfile.py
*.md documentation files

⚠️ Agents must ask before modifying:
onprem-aws/infrastructure/terraform/versions.tf
onprem-aws/infrastructure/terraform/variables.tf
onprem-aws/infrastructure/terraform/backend.tf
onprem-aws/infrastructure/terraform/environments/prod/**
onprem-aws/infrastructure/terraform/environments/_imported-demo/**   # demo state owner
onprem-aws/infrastructure/terraform/imports/**                        # demo import blocks
Any file affecting IAM roles or policies

❌ Agents must never modify:
.env files
terraform.tfvars
*.pem key files
.aws/ credential files
terraform.tfstate
onprem-aws/infrastructure/discovery/**                     # gitignored account dump
Any file in .git/
```

---

## Rules for Runtime Agents (Bedrock)

### Data Analysis Agent

**Purpose:** Analyze IoT telemetry data, calculate KPIs, detect anomalies, and generate operational insights.

**Framework:** Amazon Bedrock Claude (Converse API).

**Allowed actions:**
- Query Amazon OpenSearch index `csv_telemetry_vecs` via kNN search
- Read from `s3://iot-mining-poc/curated/` and `s3://iot-mining-poc/approved/`
- Calculate KPIs: fuel efficiency, vehicle utilization, idle rate, MTBF, MTTR, haul cycle time, payload utilization, equipment availability
- Generate business-readable summaries of fleet insights
- Flag anomalies and inefficiencies

**Prohibited actions:**
- Read from `s3://iot-mining-poc/raw/` — raw data is untrusted
- Write to any S3 prefix other than `s3://iot-mining-poc/logs/`
- Modify OpenSearch indices or mappings
- Invoke other AWS services not listed above
- Return customer data from one tenant to another tenant's session
- Fabricate KPI values — always derive from actual data, never hallucinate metrics

**KPI Definitions (must follow exactly):**

```
fuel_efficiency     = SUM(distance_km) / SUM(fuel_litres)              [km/L]
vehicle_utilization = (active_hours / scheduled_hours) * 100           [%]
cost_per_km         = SUM(total_cost) / SUM(distance_km)               [currency/km]
maintenance_compliance = (on_time_services / total_scheduled) * 100    [%]
MTBF                = total_operating_hours / number_of_failures        [hours]
idle_rate           = (idle_hours / engine_on_hours) * 100              [%]
OTD                 = (on_time_deliveries / total_deliveries) * 100     [%]
co2_per_km          = SUM(co2_grams) / SUM(distance_km)                [g/km]
```

**Guardrail:** Always apply `iot-mining-poc-guardrail-v1` before processing user queries and before returning responses.

---

### RAG Compliance Agent

**Purpose:** Answer natural language compliance questions grounded in regulatory documents across multiple jurisdictions (Senegal, United States, Chile).

**Service:** Amazon Bedrock Claude (Converse API) with hybrid search over OpenSearch Serverless. Selectable model: `us.anthropic.claude-sonnet-4-6` (default), `us.amazon.nova-pro-v1:0`, or `deepseek.v3.2`.

> **PDF pipeline classifier:** uses `us.anthropic.claude-haiku-4-5-20251001-v1:0` (GRANTED in this account). Extractor uses Sonnet 4.6.

**Allowed actions:**
- Query Amazon OpenSearch index `pdf_legal_vecs` via hybrid search (kNN + BM25)
- Read from `s3://iot-mining-poc/approved/` and `s3://iot-mining-poc/vector-input/`
- Generate grounded answers with traceable citations to source documents
- Return side-by-side model comparisons when benchmarking is enabled

**Prohibited actions:**
- Fabricate citations or regulatory references — every claim must trace to an ingested document
- Return information from jurisdiction A when asked about jurisdiction B
- Access `s3://iot-mining-poc/raw/` directly
- Modify or delete documents in OpenSearch
- Provide legal advice — outputs are advisory only, not legal counsel

**Citation format (must follow):**
```
[Source: {document_name}, {jurisdiction}, {article_or_section_reference}, page {N}]
```

**Guardrail:** Always apply `iot-mining-poc-guardrail-v1` on:
- User query before retrieval
- Retrieved chunks before passing to LLM
- Final response before returning to user

---

## Bedrock Guardrail Specification

**Guardrail name:** `iot-mining-poc-guardrail-v1`
**Applied at:** All AI touchpoints (see above)

### Prompt Attack Detection (BLOCK)
- Attempts to reveal system prompts or instructions
- Attempts to bypass access control or role boundaries
- Instructions to return all indexed documents
- Instructions to call hidden or undocumented tools
- Instructions to modify OpenSearch indices or S3 objects
- Instructions to trigger ingestion pipelines directly
- Jailbreak attempts or persona override instructions

### Sensitive Information Filtering (ANONYMIZE or BLOCK)
- Email addresses
- Phone numbers
- Physical addresses
- Employee IDs
- Contract IDs
- Site IDs
- Any mining-specific operational identifiers

### Topic Denial
- Legal advice (RAG agent provides regulatory information, not legal counsel)
- Financial advice
- Medical advice
- Content unrelated to mining operations, fleet management, or regulatory compliance

---

## S3 Data Flow — Agent Responsibilities

Agents must respect the following data lifecycle at all times:

```
raw/          ← External input — NO agent reads from here for AI processing
    ↓ (validation Lambda)
quarantine/   ← Failed checks — agents do not process this data
approved/     ← Passed validation — agents MAY read
    ↓ (guardrail check)
vector-input/ ← Guardrail-passed — safe for Bedrock embedding
    ↓ (embedding pipeline)
OpenSearch    ← Indexed vectors — agents query here

logs/         ← All agents WRITE audit logs here only
```

**Rule:** If data has not passed through `approved/` → `vector-input/`, it must not be used as context for any Bedrock model or agent.

---

## Security Rules — All Agents

These rules apply universally to all agents, development-time and runtime:

1. **No secret exposure** — never include AWS credentials, API keys, or tokens in any output, log, or generated file
2. **No cross-tenant data access** — never return data belonging to tenant A in a session for tenant B
3. **No raw data to Bedrock** — `raw/` prefix is untrusted and must never reach embedding models or agents
4. **Guardrails are non-negotiable** — never suggest, generate, or accept code that bypasses `iot-mining-poc-guardrail-v1`
5. **Audit everything** — all agent actions that read or write data must produce a log entry in `s3://iot-mining-poc/logs/`
6. **Least privilege** — agents must only request and use the minimum IAM permissions required for their specific task
7. **No fabrication** — agents must never invent KPI values, regulatory citations, or operational data

---

## Agent Output Standards

### Data Analysis Agent responses must include:
```json
{
  "kpis": { ... },
  "anomalies": [ ... ],
  "insights": "Business-readable summary",
  "data_period": "ISO 8601 range",
  "confidence": "high | medium | low",
  "data_gaps": [ ... ]
}
```

### RAG Compliance Agent responses must include:
```json
{
  "answer": "Grounded response text",
  "citations": [
    {
      "document": "document_name",
      "jurisdiction": "senegal | us | chile",
      "reference": "Article 45.3",
      "page": 12
    }
  ],
  "confidence": "high | medium | low",
  "disclaimer": "This response is advisory only and does not constitute legal advice."
}
```

### Development Agent commits must follow:
```
[BHMIB-{ticket}] {type}: {description}
```
See `CLAUDE.md` for full commit and branching conventions.

---

## Adding New Agents

When adding a new Bedrock Agent or AI component to the platform:

1. Add an entry to the Agent Inventory table above
2. Define its allowed and prohibited actions clearly
3. Specify which S3 prefixes it may read/write
4. Confirm it applies `iot-mining-poc-guardrail-v1` at all touchpoints
5. Define its output schema
6. Open a PR with the updated `AGENTS.md` using commit format:
   `[BHMIB-{ticket}] docs: add {agent-name} agent specification to AGENTS.md`

---

## Related Files

- `CLAUDE.md` — Claude Code specific instructions, IaC strategy, git conventions
- `infrastructure/cloudformation/bedrock-guardrails/` — Guardrail stack
- `backend/agents/data-analysis/` — Data Analysis Agent implementation
- `backend/agents/rag-agent/` — RAG Compliance Agent implementation
- `fabfile.py` — Fabric orchestrator: `env.*` lifecycle (up, plan, down, endpoints, health), `lambda.*` pipeline ops, `bedrock.*` model probing, `opensearch.*` status, `frontend.*` Amplify deploy, `docs.*` mkdocs-material build+deploy, `ollama.*` EC2 remote ops
- `lambdas/api/handler.py` — Lambda API: GET /fleet/assets, /kpis, /fuel/*, /maintenance/*, /telemetry/* (datos S3, sin LLM) + POST /analyze (FleetAgent) + POST /chat (RAGAgent)
- `cloudformation/apigw/apigw.yaml` — HTTP API v2 con CORS nativo para `*.amplifyapp.com`
- `scripts/discover-aws.sh` / `.ps1` — read-only AWS inventory for demo import
