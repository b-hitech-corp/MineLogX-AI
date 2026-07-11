# Data Analysis Agent

The Data Analysis Agent analyzes IoT telemetry data from mining equipment, calculates KPIs,
detects anomalies, and generates operational insights for fleet and site managers.

---

## Overview

| Property | Value |
|---|---|
| Service | Amazon Bedrock Claude (Converse API) |
| Model | `us.anthropic.claude-sonnet-4-6` (default) |
| Entry point | `POST /analyze` via API Lambda |
| Index | `csv_telemetry_vecs` (OpenSearch Serverless) |

---

## Allowed Actions

- Query `csv_telemetry_vecs` via kNN + BM25 hybrid search
- Read from `s3://minelogx-<env>-telemetry-data/curated/` and `.../approved/`
- Calculate KPIs from retrieved telemetry vectors
- Generate business-readable summaries of fleet insights
- Flag anomalies and inefficiencies

## Prohibited Actions

- Read from `raw/` prefix — raw data is untrusted
- Write to any S3 prefix other than `logs/`
- Modify OpenSearch indices or mappings
- Return data from one tenant in another tenant's session
- Fabricate KPI values — all metrics must be derived from actual ingested data

---

## KPI Definitions

All KPI calculations follow these exact formulas. The agent must not deviate.

See the [KPI Definitions](kpi-definitions.md) page for the full table.

---

## Output Schema

Every response from the Data Analysis Agent must follow this structure:

```json
{
  "kpis": {
    "fuel_efficiency": 3.2,
    "vehicle_utilization": 78.5,
    "idle_rate": 12.3
  },
  "anomalies": [
    {
      "vehicle_id": "TRK-042",
      "signal": "idle_rate > 30%",
      "severity": "high"
    }
  ],
  "insights": "Fleet utilization is at 78.5% with TRK-042 showing an idle rate of 31% — investigate shift scheduling.",
  "data_period": "2026-06-01T00:00:00Z/2026-07-01T00:00:00Z",
  "confidence": "high",
  "data_gaps": ["TRK-017 — no GPS signal for 48 h on 2026-06-15"]
}
```

---

## Guardrail

`iot-mining-poc-guardrail-v1` is applied:

1. Before processing the user query
2. Before returning the response to the caller

See [Guardrails](guardrails.md) for the full specification.
