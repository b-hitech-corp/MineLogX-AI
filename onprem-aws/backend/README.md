# onprem-aws/backend

Application code for the AWS target: the target-architecture pipelines/agents and
the thin Lambda handlers that wire them into the IaC (see the diagram in
`docs/architecture/`).

## Layout

```
backend/
├── lambdas/
│   └── api/handler.py          # → minelogx-<env>-api  (Frontend & API Layer)
│                               #   routes POST /analyze → FleetAgent
│                               #         POST /chat    → BedrockRAGAgent
├── csv_pipeline/               # CSV Vectorization Pipeline (batch)
│   ├── agent/csv_vectorization_pipeline.py   # run_pipeline(file_path, stages=...)
│   └── lambda_function.py      # → minelogx-<env>-csv  (invoked per stage by Step Functions)
├── pdf_pipeline/               # PDF Vectorization Pipeline (event-driven)
│   └── agent/pdf_vectorization_pipeline.py   # ships its own lambda_handler
│                               # → minelogx-<env>-pdf  (S3 PutObject → EventBridge)
├── data_analysis_agent/        # Bedrock Claude — telemetry KPIs / insights (routed by api)
├── rag_agent/                  # Bedrock — compliance Q&A over OpenSearch (routed by api)
│   └── bedrock_rag_agent.py    # BedrockRAGAgent: multi-model (Claude 4.6 / Nova Pro / DeepSeek V3.2)
├── agents/                     # Bedrock agent definitions
└── requirements.txt            # shared runtime deps (dev/test)
```

## Lambda architecture

Three Lambda functions in total:

| Function             | Handler                                                        | Trigger           |
|----------------------|----------------------------------------------------------------|-------------------|
| `minelogx-<env>-api` | `handler.lambda_handler`                                       | API Gateway (HTTP) |
| `minelogx-<env>-csv` | `csv_pipeline.lambda_function.lambda_handler`                  | Step Functions (per stage) |
| `minelogx-<env>-pdf` | `pdf_pipeline.agent.pdf_vectorization_pipeline.lambda_handler` | EventBridge (S3 `Object Created`, `.pdf`) |

### API routes

| Method | Path       | Agent              | Description                                    |
|--------|------------|--------------------|------------------------------------------------|
| GET    | `/health`  | —                  | Health check (no AI call)                      |
| POST   | `/analyze` | `FleetAgent`       | Telemetry KPI query (fleet / fuel / tonnage …) |
| POST   | `/chat`    | `BedrockRAGAgent`  | Compliance Q&A over OpenSearch dual-index      |

`/chat` accepts an optional `model` field: `"claude-sonnet-4.6"` (default),
`"nova-pro"`, or `"deepseek-v3.2"` — selects the Bedrock model end-to-end.

## Conventions
- Runtime **Python 3.11**.
- Keep handlers thin — the real logic lives in the `*_pipeline` / `*_agent`
  packages. Cloud-agnostic logic goes in the repo-root `shared/`.
- Guardrail (`GUARDRAIL_ID`) must be applied at every AI touchpoint.
- Agent singletons in `api/handler.py` are module-level (survive warm invocations).

> **PDF pipeline shape:** the diagram draws the PDF path as several Lambdas
> (classify → extract → embed). The code collapses that into a *single* Lambda
> (`run_pipeline` does classify → Textract/Claude → Titan → ingest in one call),
> so the IaC wires one PDF Lambda. Splitting it into multiple functions is a
> future refactor if throughput/timeout demands it.

## How it reaches AWS

The Terraform `lambda` module (and the CloudFormation `lambda` stack) zip these
folders and wire them to the functions, then `fab env.up` deploys. Triggers,
roles (Bedrock/AOSS/S3/Textract), the CSV Step Functions state machine, and the
EventBridge scheduler/rule are all provisioned by `modules/env_stack` /
`cloudformation/parent.yaml`.

## Runtime dependencies

| Lambda | Layer | Build command |
|--------|-------|---------------|
| `api`  | — (stdlib + boto3 only at handler level; agents loaded from layer) | — |
| `csv`  | `minelogx-<env>-csv-deps` | `fab lambda.build-layer csv` |
| `pdf`  | `minelogx-<env>-pdf-deps` | `fab lambda.build-layer pdf` |

Lambda zips contain *code only*. Build layers before deploying with
`--build-csv-layer` / `--build-pdf-layer` flags on `fab env.up`.
