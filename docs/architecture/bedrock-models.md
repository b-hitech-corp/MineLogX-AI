# Bedrock Models

MineLogX AI uses Amazon Bedrock for inference. This page lists all models in use,
their pipeline assignment, and access requirements.

---

## Model Table

| Model ID | Use case | Pipeline |
|---|---|---|
| `us.anthropic.claude-sonnet-4-6` | Data analysis, CSV annotation, complex PDF extraction, RAG Q&A | API Lambda, CSV Pipeline, PDF Pipeline |
| `us.amazon.nova-pro-v1:0` | RAG Compliance Q&A (selectable via UI) | RAG Agent |
| `deepseek.v3.2` | RAG Compliance Q&A (selectable via UI) | RAG Agent |
| `cohere.embed-multilingual-v3` | Telemetry vectorization (1024 dimensions) | CSV Pipeline |
| `amazon.titan-embed-text-v2:0` | Legal document vectorization (1536 dimensions) | PDF Pipeline |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | PDF document classification (Signal 1) | PDF Pipeline — GRANTED in this account |

---

## Cross-Region Inference Profiles

All Claude and Nova models use **cross-region inference profiles** with the `us.` prefix.
Using bare model IDs (e.g. `anthropic.claude-3-5-sonnet-20241022-v2:0`) raises
`ResourceNotFoundException` in this account.

```python
# Correct
model_id = "us.anthropic.claude-sonnet-4-6"

# Wrong — will raise ResourceNotFoundException
model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"
```

---

## Checking Model Access

```bash
uv run fab bedrock.model-access
# Probes all project models — prints GRANTED / DENIED per model
```

To enable a denied model: AWS Console → Bedrock → Model access → Manage model access.

---

## Changing a Model at Runtime

Use `bedrock.set-model` to switch a pipeline's model without a full redeploy:

```bash
# Switch the API Lambda to Nova Pro for RAG responses
uv run fab bedrock.set-model api dev us.amazon.nova-pro-v1:0

# Switch the PDF classifier back to Haiku 4.5
uv run fab bedrock.set-model pdf-haiku dev us.anthropic.claude-haiku-4-5-20251001-v1:0
```

Available pipeline aliases: `api`, `api-nova`, `api-deepseek`, `csv`, `csv-embed`, `pdf`, `pdf-haiku`, `pdf-embed`.

---

## Guardrail

A single reusable guardrail (`iot-mining-poc-guardrail-v1`) is applied at every AI touchpoint.
See [Guardrails](../agents/guardrails.md) for the full specification.
