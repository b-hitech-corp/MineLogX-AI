# Guardrails

MineLogX AI uses a single reusable Bedrock Guardrail applied at every AI touchpoint.

---

## Guardrail Specification

| Property | Value |
|---|---|
| Name | `iot-mining-poc-guardrail-v1` |
| Provisioned by | CloudFormation `bedrock-guardrails/bedrock-guardrails.yaml` |
| Applied at | User queries, retrieved chunks, final responses (all agents) |

---

## Prompt Attack Detection (BLOCK)

The guardrail blocks any input that attempts to:

- Reveal system prompts or internal instructions
- Bypass access control or role boundaries
- Return all indexed documents indiscriminately
- Call hidden or undocumented tools
- Modify OpenSearch indices or S3 objects
- Trigger ingestion pipelines directly from the user-facing interface
- Override the agent's persona (jailbreak attempts)

---

## Sensitive Information Filtering

The following are anonymized (`ANONYMIZE`) or blocked (`BLOCK`) automatically:

| Category | Action |
|---|---|
| Email addresses | ANONYMIZE |
| Phone numbers | ANONYMIZE |
| Physical addresses | ANONYMIZE |
| Employee IDs | ANONYMIZE |
| Contract IDs | ANONYMIZE |
| Site IDs | ANONYMIZE |
| Mining-specific operational identifiers | ANONYMIZE |

---

## Topic Denial

Requests outside the platform's scope are rejected:

| Denied topic | Reason |
|---|---|
| Legal advice | RAG agent provides regulatory information only — not legal counsel |
| Financial advice | Out of scope |
| Medical advice | Out of scope |
| Content unrelated to mining operations, fleet management, or regulatory compliance | Out of scope |

---

## Where Guardrails Are Applied

| Touchpoint | Both agents | CSV Pipeline | PDF Pipeline |
|---|---|---|---|
| User query | ✅ | — | — |
| Retrieved chunks (before LLM context) | ✅ | — | — |
| Final response | ✅ | — | — |
| CSV chunk before embedding | — | ✅ | — |
| PDF section before embedding | — | — | ✅ |

!!! danger "Guardrails are non-negotiable"
    Never suggest, generate, or accept code that bypasses `iot-mining-poc-guardrail-v1`.
    All AI touchpoints must pass guardrail evaluation. This is enforced in the Lambda handlers
    and is a critical architecture constraint.
