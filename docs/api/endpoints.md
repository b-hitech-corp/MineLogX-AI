# API Endpoints

The API Lambda (`minelogx-<env>-api`) serves all requests from the frontend via HTTP API Gateway v2.

**Base URL (dev):** `https://f81kmc7x2d.execute-api.us-east-1.amazonaws.com/dev`

Get the live URL for any environment:

```bash
uv run fab env.endpoints dev
```

---

## Data Endpoints (GET — no AI call)

These endpoints read directly from S3 curated data. No Bedrock call is made.
Response time target: < 5 seconds.

### `GET /kpis`

Returns aggregated fleet KPI metrics.

```json
[
  { "metric": "fuel_efficiency", "value": 3.2, "unit": "km/L" },
  { "metric": "vehicle_utilization", "value": 78.5, "unit": "%" },
  { "metric": "idle_rate", "value": 12.3, "unit": "%" }
]
```

---

### `GET /fleet/assets`

Returns a list of fleet equipment records (up to 500 items).

```json
[
  {
    "vehicle_id": "TRK-042",
    "type": "haul_truck",
    "site": "C1",
    "status": "active",
    "last_seen": "2026-07-09T14:32:00Z"
  }
]
```

---

### `GET /fuel/records`

Returns fuel consumption records (up to 500 items).

---

### `GET /fuel/trend`

Returns time-series fuel consumption trend data.

---

### `GET /maintenance/items`

Returns pending and completed maintenance items.

---

### `GET /maintenance/work-orders`

Returns open maintenance work orders.

---

### `GET /telemetry/gps`

Returns GPS location records for all tracked vehicles.

---

### `GET /telemetry/zones`

Returns geofence zone definitions and current occupancy.

---

## AI Endpoints (POST — Bedrock call)

### `POST /chat`

Compliance Q&A powered by the RAG Compliance Agent.
Retrieves from `pdf_legal_vecs` (regulatory, shared) and `csv_telemetry_vecs` (telemetry, scoped by `client`).

**Request:**
```json
{
  "query": "What are the dust exposure limits in Chilean mining regulations?",
  "model": "claude-sonnet-4.6",
  "client": "C1"
}
```

| Field | Required | Notes |
|---|---|---|
| `query` | Yes | Also accepted as `message` (legacy) |
| `client` | Yes | Scopes telemetry retrieval to this client's data. Missing → telemetry index skipped |
| `model` | No | `"claude-sonnet-4.6"` (default), `"nova-pro"`, `"deepseek-v3.2"` |

**Response:**
```json
{
  "answer": "According to Article 45.3 of the Chilean Mining Safety Regulations...",
  "citations": [
    {
      "document": "Chile_Mining_Safety_DS132.pdf",
      "jurisdiction": "chile",
      "reference": "Article 45.3",
      "page": 78
    }
  ],
  "confidence": "high",
  "disclaimer": "This response is advisory only and does not constitute legal advice."
}
```

---

### `POST /analyze`

Fleet telemetry analysis powered by the Data Analysis Agent.
Retrieves from `csv_telemetry_vecs` and calculates KPIs.

**Request:**
```json
{
  "query": "What is the fuel efficiency of the fleet in site C1 for the last 30 days?"
}
```

**Response:**
```json
{
  "kpis": { "fuel_efficiency": 3.2 },
  "anomalies": [],
  "insights": "Site C1 fleet fuel efficiency is 3.2 km/L, within the expected range.",
  "data_period": "2026-06-01T00:00:00Z/2026-07-01T00:00:00Z",
  "confidence": "high",
  "data_gaps": []
}
```

---

## CORS

API Gateway is configured with `AllowOrigins: ['*']` and handles OPTIONS preflight in the Lambda handler.

## Error Responses

All errors return JSON with a `statusCode` and `message`:

```json
{ "statusCode": 500, "message": "Internal server error" }
```

---

## Validating All Routes

```bash
uv run fab frontend.validate dev
# Checks each GET route for HTTP 200, JSON content-type, and CORS headers
```
