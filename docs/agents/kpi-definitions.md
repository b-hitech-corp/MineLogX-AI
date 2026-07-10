# KPI Definitions

The Data Analysis Agent must follow these exact KPI formulas. Fabricated or approximated
values are a critical violation — all metrics must be derived from actual ingested data.

---

## Fleet KPI Formulas

| KPI | Formula | Unit |
|---|---|---|
| **Fuel Efficiency** | `SUM(distance_km) / SUM(fuel_litres)` | km/L |
| **Vehicle Utilization** | `(active_hours / scheduled_hours) × 100` | % |
| **Cost per km** | `SUM(total_cost) / SUM(distance_km)` | currency/km |
| **Maintenance Compliance** | `(on_time_services / total_scheduled) × 100` | % |
| **MTBF** | `total_operating_hours / number_of_failures` | hours |
| **Idle Rate** | `(idle_hours / engine_on_hours) × 100` | % |
| **OTD (On-Time Delivery)** | `(on_time_deliveries / total_deliveries) × 100` | % |
| **CO₂ per km** | `SUM(co2_grams) / SUM(distance_km)` | g/km |

---

## Field Mapping

The CSV telemetry data across C1, C2, and C3 datasets uses different column names for the same
signals. The CSV pipeline schema inspector normalizes these to a canonical schema before embedding.

After normalization, the canonical fields are:

| Canonical field | Description |
|---|---|
| `vehicle_id` | Equipment identifier |
| `timestamp` | UTC ISO 8601 |
| `distance_km` | Distance traveled in this record interval |
| `fuel_litres` | Fuel consumed in this record interval |
| `active_hours` | Engine-on time in productive operation |
| `idle_hours` | Engine-on time with no productive output |
| `engine_on_hours` | Total engine-on time (active + idle) |
| `scheduled_hours` | Expected operation window |
| `total_cost` | Operating cost for this interval |
| `co2_grams` | CO₂ emissions in this interval |
| `failures` | Failure events (binary or count) |
| `deliveries_total` | Haul deliveries attempted |
| `deliveries_on_time` | Haul deliveries completed within SLA |

---

## Anomaly Detection

Anomalies are flagged when a KPI falls outside expected operational bounds.
Typical thresholds (configurable per site):

| KPI | Anomaly signal |
|---|---|
| Idle Rate | > 25% for a single vehicle in a shift |
| Fuel Efficiency | < 2.0 km/L (possible fuel leak or overload) |
| Vehicle Utilization | < 60% without scheduled maintenance |
| MTBF | < 200 hours (frequent failures) |

!!! note
    Thresholds are not hardcoded. The agent uses vector similarity to compare current readings
    against historical baselines in `csv_telemetry_vecs`, so the anomaly signal adapts to
    each site's operating conditions.
