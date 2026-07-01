# MineLogX API - Endpoints Definition

## Base URL
```
https://<api-gateway-id>.execute-api.us-east-1.amazonaws.com/demo
```

---

## 1. Overview / KPIs

```
GET  /api/kpis/summary       → Fleet Performance, Asset Health, Operational Efficiency, Sustainability
GET  /api/kpis/trends        → Time-series data para charts
GET  /api/kpis/ai-insights   → AI recommendations panel
```

### Response example - /api/kpis/summary
```json
{
  "period": "daily",
  "timestamp": "2026-06-03T00:00:00Z",
  "fleet_performance": {
    "tonnes_moved": 8420,
    "tonnes_per_truck": 221,
    "completed_cycles": 312,
    "avg_haul_cycle_time": 27.4
  },
  "asset_health": {
    "equipment_availability": 86,
    "fleet_utilization": 82,
    "maintenance_compliance": 71,
    "overdue_count": 2
  },
  "operational_efficiency": {
    "fuel_per_tonne": 3.8,
    "cycle_efficiency": 82,
    "idle_time_avg": 18
  },
  "sustainability": {
    "total_fuel_consumed": 4250,
    "co2_emissions": 11220
  }
}
```

---

## 2. Fleet

```
GET  /api/fleet/vehicles      → Lista de vehículos + status
GET  /api/fleet/{id}          → Detalle de un vehículo
GET  /api/fleet/anomalies     → Alertas y anomalías detectadas
```

### Response example - /api/fleet/vehicles
```json
{
  "vehicles": [
    {
      "id": "TRUCK-01",
      "status": "active",
      "fuel_level": 72,
      "location": { "lat": -23.5, "lon": -46.6 },
      "anomaly": false
    },
    {
      "id": "TRUCK-02",
      "status": "idle",
      "fuel_level": 45,
      "location": { "lat": -23.6, "lon": -46.7 },
      "anomaly": true,
      "anomaly_type": "excessive_idle"
    }
  ]
}
```

---

## 3. Maintenance

```
GET  /api/maintenance/schedule     → Scheduled maintenance tasks
GET  /api/maintenance/compliance   → Maintenance compliance %
GET  /api/maintenance/predictions  → Predictive maintenance alerts
```

### Response example - /api/maintenance/predictions
```json
{
  "predictions": [
    {
      "asset_id": "EX-12",
      "risk_level": "high",
      "predicted_failure_hours": 36,
      "recommendation": "Schedule maintenance within 36 operating hours"
    }
  ]
}
```

---

## 4. Fuel

```
GET  /api/fuel/summary       → Fuel burn rate, CO2 per km
GET  /api/fuel/by-vehicle    → Fuel consumption por vehículo
GET  /api/fuel/anomalies     → Consumo anormal detectado
```

### Response example - /api/fuel/anomalies
```json
{
  "anomalies": [
    {
      "vehicle_id": "TRUCK-204",
      "increase_percent": 18,
      "vs_period": "7-day average",
      "alert": "Fuel consumption on Truck 204 increased 18% vs 7-day average"
    }
  ]
}
```

---

## 5. GPS / Location

```
GET  /api/location/vehicles  → Posición actual de vehículos
GET  /api/location/heatmap   → Actividad por zona/pit
```

### Response example - /api/location/vehicles
```json
{
  "vehicles": [
    {
      "id": "TRUCK-01",
      "lat": -23.5505,
      "lon": -46.6333,
      "speed_kmh": 45,
      "heading": "north",
      "zone": "Pit-A"
    }
  ]
}
```

---

## 6. Load / Tonnage

```
GET  /api/tonnage/summary    → Tonnes moved, tonnes/truck
GET  /api/tonnage/by-route   → Tonnes per route
GET  /api/tonnage/cycle-time → Haul cycle time
```

### Response example - /api/tonnage/summary
```json
{
  "total_tonnes_moved": 8420,
  "tonnes_per_truck": 221,
  "tonnes_per_route": {
    "Route-A": 3200,
    "Route-B": 2800,
    "Route-C": 2420
  },
  "avg_cycle_time_min": 27.4
}
```

---

## 7. Safety

```
GET  /api/safety/events      → Safety/fatigue events
GET  /api/safety/risk-score  → Risk indicators por vehículo
```

### Response example - /api/safety/events
```json
{
  "events": [
    {
      "vehicle_id": "TRUCK-03",
      "event_type": "fatigue_alert",
      "severity": "high",
      "timestamp": "2026-06-03T14:32:00Z",
      "operator_id": "OP-007"
    }
  ]
}
```

---

## 8. Compliance / AI Chat

```
POST /api/chat/query    → Query a Qwen3 + Gemma3 simultáneo
POST /api/chat/search   → RAG con mxbai-embed-large
GET  /api/chat/history  → Historial de queries
```

### Request - /api/chat/query
```json
{
  "query": "What are the fatigue regulations for truck operators in Senegal?",
  "jurisdiction": "senegal"
}
```

### Response - /api/chat/query
```json
{
  "query": "What are the fatigue regulations for truck operators in Senegal?",
  "responses": {
    "qwen3": {
      "model": "qwen3:8b",
      "response": "According to Senegalese regulation...",
      "sources": ["Article 45.3", "Section 12.1"],
      "time_ms": 1200
    },
    "gemma3": {
      "model": "gemma3:12b",
      "response": "Under Senegalese law...",
      "sources": ["Article 45.3"],
      "time_ms": 980
    }
  }
}
```

### Request - /api/chat/search
```json
{
  "text": "fatigue management mining operators",
  "top_k": 5
}
```

### Response - /api/chat/search
```json
{
  "results": [
    {
      "score": 0.94,
      "text": "Relevant regulation excerpt...",
      "source": "Senegal Mining Act, Article 45.3"
    }
  ]
}
```

---

## Priority for June 5 Demo

| Priority | Endpoint | Reason |
|---|---|---|
| MUST | GET /api/kpis/summary | Main dashboard page |
| MUST | GET /api/fleet/vehicles | Fleet view |
| MUST | GET /api/fleet/anomalies | Shows AI value |
| MUST | POST /api/chat/query | LLM benchmarking demo |
| NICE | GET /api/fuel/summary | Fuel management |
| NICE | GET /api/maintenance/predictions | Predictive maintenance |
| LATER | GET /api/location/vehicles | GPS/map view |
| LATER | POST /api/chat/search | Full RAG |

---

## Lambda Functions Required

| Lambda | Endpoint | Model/Source |
|---|---|---|
| minelogx-kpis | GET /api/kpis/* | S3 synthetic data |
| minelogx-fleet | GET /api/fleet/* | S3 synthetic data |
| minelogx-maintenance | GET /api/maintenance/* | S3 synthetic data |
| minelogx-fuel | GET /api/fuel/* | S3 synthetic data |
| minelogx-location | GET /api/location/* | S3 synthetic data |
| minelogx-tonnage | GET /api/tonnage/* | S3 synthetic data |
| minelogx-safety | GET /api/safety/* | S3 synthetic data |
| minelogx-chat | POST /api/chat/* | Qwen3 + Gemma3 + mxbai |

---

## Model Endpoints (EC2 Ollama)

```
QWEN3_ENDPOINT      = http://ec2-98-81-228-187.compute-1.amazonaws.com:11434
GEMMA3_ENDPOINT     = http://ec2-100-31-82-64.compute-1.amazonaws.com:11434
EMBEDDINGS_ENDPOINT = http://ec2-3-208-23-94.compute-1.amazonaws.com:11434
```
