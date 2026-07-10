# RAG Compliance Agent

The RAG Compliance Agent answers natural language regulatory questions grounded in
jurisdiction-specific legal documents. Every claim must trace to an ingested document.

---

## Overview

| Property | Value |
|---|---|
| Service | Amazon Bedrock Claude (Converse API + OpenSearch hybrid search) |
| Default model | `us.anthropic.claude-sonnet-4-6` |
| Selectable models | `us.amazon.nova-pro-v1:0`, `deepseek.v3.2` |
| Entry point | `POST /chat` via API Lambda |
| Regulatory index | `pdf_legal_vecs` — shared across all clients |
| Telemetry index | `csv_telemetry_vecs` — filtered by `client` field |
| Jurisdictions | Senegal, United States, Chile |
| Domain scope | Mining, fleet management, and telemetry data flows only |

---

## Request Format

```json
{
  "query": "What are the dust exposure limits in Chilean mining regulations?",
  "model": "claude-sonnet-4.6",
  "client": "C1"
}
```

| Field | Required | Description |
|---|---|---|
| `query` | Yes | Natural language question (also accepted as `message` for legacy clients) |
| `model` | No | Model override. Valid: `"claude-sonnet-4.6"` (default), `"nova-pro"`, `"deepseek-v3.2"` |
| `client` | Yes | Client identifier (`^[A-Za-z0-9_-]{1,64}$`). Used to scope telemetry retrieval to that client's data |

### Client Isolation

The agent queries two indexes per request:

- **`pdf_legal_vecs`** — shared regulatory documents, no client filter applied
- **`csv_telemetry_vecs`** — filtered by a `source_file` prefix (`<client>/`) so only that client's telemetry is retrieved

**Fail-closed:** if `client` is missing or fails validation, the telemetry index is skipped entirely. The answer is grounded only on regulatory documents.

### Domain Scope

The system prompt bounds the assistant to: mining operations, fleet management, and telemetry data flows. Out-of-scope questions are politely declined.

### Model Selection

Valid values: `"claude-sonnet-4.6"` (default), `"nova-pro"`, `"deepseek-v3.2"`.

The PDF pipeline classifier uses a dedicated model:
`us.anthropic.claude-haiku-4-5-20251001-v1:0` (GRANTED in this account).

---

## Allowed Actions

- Query `pdf_legal_vecs` via hybrid search (kNN + BM25)
- Read from `s3://minelogx-<env>-legislation-documents/approved/` and `.../vector-input/`
- Generate grounded answers with traceable citations to source documents
- Return side-by-side model comparisons when benchmarking is enabled

## Prohibited Actions

- Fabricate citations or regulatory references — every claim must trace to an ingested document
- Return information from jurisdiction A when asked about jurisdiction B
- Access `raw/` prefix directly
- Modify or delete documents in OpenSearch
- Provide legal advice — outputs are advisory only

---

## Citation Format

Every cited claim must include the full source reference:

```
[Source: {document_name}, {jurisdiction}, {article_or_section_reference}, page {N}]
```

Example:
```
[Source: Senegal_Mining_Code_2016.pdf, senegal, Article 45.3, page 78]
```

---

## Output Schema

```json
{
  "answer": "According to Article 45.3 of the Senegal Mining Code (2016), ...",
  "citations": [
    {
      "document": "Senegal_Mining_Code_2016.pdf",
      "jurisdiction": "senegal",
      "reference": "Article 45.3",
      "page": 78
    }
  ],
  "confidence": "high",
  "disclaimer": "This response is advisory only and does not constitute legal advice."
}
```

---

## Guardrail

`iot-mining-poc-guardrail-v1` is applied at three points:

1. User query — before retrieval
2. Retrieved chunks — before passing to the LLM as context
3. Final response — before returning to the user

See [Guardrails](guardrails.md) for the full specification.
