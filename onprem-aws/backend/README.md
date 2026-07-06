# onprem-aws/backend

Application code for the AWS target: the target-architecture pipelines/agents and
the thin Lambda handlers that wire them into the IaC (see the diagram in
`docs/architecture/`).

## Layout

```
backend/
├── lambdas/
│   └── api/handler.py          # → minelogx-<env>-api  (Frontend & API Layer)
│                               #   thin stub; base for the pulled demo API code
├── csv_pipeline/               # CSV Vectorization Pipeline (batch)
│   ├── agent/csv_vectorization_pipeline.py   # run_pipeline(file_path, stages=...)
│   └── lambda_function.py      # → minelogx-<env>-csv  (invoked per stage by Step Functions)
├── pdf_pipeline/               # PDF Vectorization Pipeline (event-driven)
│   └── agent/pdf_vectorization_pipeline.py   # ships its own lambda_handler
│                               # → minelogx-<env>-pdf  (S3 PutObject → EventBridge)
├── data_analysis_agent/        # Bedrock Claude — telemetry KPIs / insights (used by api)
├── rag_agent/                  # Bedrock — compliance Q&A over OpenSearch (used by api)
├── agents/                     # Bedrock agent definitions
└── requirements.txt            # shared runtime deps
```

Each Lambda maps to a handler:

| Function            | Handler                                                    | Trigger |
|---------------------|------------------------------------------------------------|---------|
| `minelogx-<env>-api`| `handler.lambda_handler`                                   | API Gateway |
| `minelogx-<env>-csv`| `csv_pipeline.lambda_function.lambda_handler`              | Step Functions (per stage) |
| `minelogx-<env>-pdf`| `pdf_pipeline.agent.pdf_vectorization_pipeline.lambda_handler` | EventBridge (S3 `Object Created`, `.pdf`) |

Conventions:
- Runtime **Python 3.11**.
- Keep handlers thin — the real logic lives in the `*_pipeline` / `*_agent`
  packages. Cloud-agnostic logic goes in the repo-root `shared/`.
- Guardrail (`GUARDRAIL_ID`) must be applied at every AI touchpoint.

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

> **Runtime dependencies (not yet packaged):** the zips contain *code only*.
> Heavy deps (pandas, pyarrow, pdfplumber, opensearch-py, strands-agents, …) must
> be supplied via a **Lambda layer** (`lambda_layer_arns` var / CFN param) or a
> **container image** before the pipelines run — otherwise they fail with
> `ModuleNotFoundError`. Tracked as a follow-up task.

The `api` Lambda is a placeholder that answers health checks; it becomes the real
router once the **deployed demo API code** is pulled into `lambdas/api/` (the
`lambda.pull` task) and the agents are wired in.
