"""
KPI formula registry for mining fleet management.

Each KPI is a pure pandas function. The LLM never computes values — it only
selects which KPIs to request and presents results. column_mapper.py handles
translating client-specific column names to the variable names used here.

Domains
-------
1. Fleet Management
2. Asset Health & Predictive Maintenance
3. Safety & Fatigue Management
4. Environmental Monitoring
5. Load & Tonnage Tracking
6. GPS / Pit Navigation
7. Compliance & Reporting
8. Maximo Integration
9. AI Assistant
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd


@dataclass
class KPIDefinition:
    name: str
    description: str
    unit: str
    formula_doc: str  # human-readable formula
    compute: Callable[[pd.DataFrame], Any]  # deterministic pandas function


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_cols(df: pd.DataFrame, *cols: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Required columns missing from dataset: {missing}")


def _div(numerator: float, denominator: float, scale: float = 1.0) -> float:
    """Safe division — returns 0.0 when denominator is zero or near-zero."""
    return round(numerator / denominator * scale, 3) if abs(denominator) > 1e-9 else 0.0


KPI_REGISTRY: dict[str, KPIDefinition] = {}


def _register(kpi: KPIDefinition) -> KPIDefinition:
    KPI_REGISTRY[kpi.name] = kpi
    return kpi


# ===========================================================================
# 1. Fleet Management
# ===========================================================================

_register(
    KPIDefinition(
        name="fuel_efficiency",
        description="Average distance travelled per litre of fuel consumed.",
        unit="km/L",
        formula_doc="fuel_efficiency = SUM(distance_km) / SUM(fuel_litres)",
        compute=lambda df: (
            _require_cols(df, "distance_km", "fuel_litres")
            or _div(df["distance_km"].sum(), df["fuel_litres"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="vehicle_utilization",
        description="Percentage of scheduled hours vehicles were actively in use.",
        unit="%",
        formula_doc="utilization = (active_hours / scheduled_hours) × 100",
        compute=lambda df: (
            _require_cols(df, "active_hours", "scheduled_hours")
            or _div(df["active_hours"].sum(), df["scheduled_hours"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="fleet_availability",
        description="Percentage of scheduled hours the fleet was available (not in downtime).",
        unit="%",
        formula_doc="availability = (available_hours / scheduled_hours) × 100",
        compute=lambda df: (
            _require_cols(df, "available_hours", "scheduled_hours")
            or _div(df["available_hours"].sum(), df["scheduled_hours"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="mean_cycle_time",
        description="Average haul cycle time from loading to dumping and returning.",
        unit="min",
        formula_doc="mean_cycle_time = MEAN(cycle_time_min)",
        compute=lambda df: (
            _require_cols(df, "cycle_time_min")
            or round(float(df["cycle_time_min"].mean()), 2)
        ),
    )
)

_register(
    KPIDefinition(
        name="haul_truck_productivity",
        description="Tonnes of material moved per operating hour.",
        unit="t/hr",
        formula_doc="productivity = SUM(payload_tonnes) / SUM(operating_hours)",
        compute=lambda df: (
            _require_cols(df, "payload_tonnes", "operating_hours")
            or _div(df["payload_tonnes"].sum(), df["operating_hours"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="payload_utilization",
        description="Actual payload as a percentage of target payload.",
        unit="%",
        formula_doc="payload_utilization = (SUM(payload_tonnes) / SUM(target_payload_tonnes)) × 100",
        compute=lambda df: (
            _require_cols(df, "payload_tonnes", "target_payload_tonnes")
            or _div(df["payload_tonnes"].sum(), df["target_payload_tonnes"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="fuel_consumption_rate",
        description="Fuel consumed per operating hour.",
        unit="L/hr",
        formula_doc="fuel_rate = SUM(fuel_litres) / SUM(operating_hours)",
        compute=lambda df: (
            _require_cols(df, "fuel_litres", "operating_hours")
            or _div(df["fuel_litres"].sum(), df["operating_hours"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="cost_per_km",
        description="Total operating cost per kilometre driven.",
        unit="$/km",
        formula_doc="cost_per_km = SUM(total_cost) / SUM(distance_km)",
        compute=lambda df: (
            _require_cols(df, "total_cost", "distance_km")
            or _div(df["total_cost"].sum(), df["distance_km"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="idle_rate",
        description="Proportion of engine-on time spent idling.",
        unit="%",
        formula_doc="idle_rate = (idle_hours / engine_on_hours) × 100",
        compute=lambda df: (
            _require_cols(df, "idle_hours", "engine_on_hours")
            or _div(df["idle_hours"].sum(), df["engine_on_hours"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="on_time_delivery",
        description="Percentage of haul deliveries completed within the scheduled window.",
        unit="%",
        formula_doc="OTD = (on_time_deliveries / total_deliveries) × 100",
        compute=lambda df: (
            _require_cols(df, "on_time_deliveries", "total_deliveries")
            or _div(df["on_time_deliveries"].sum(), df["total_deliveries"].sum(), 100)
        ),
    )
)


# ===========================================================================
# 2. Asset Health & Predictive Maintenance
# ===========================================================================

_register(
    KPIDefinition(
        name="mean_time_between_failures",
        description="Average operating hours between unplanned breakdowns.",
        unit="hr",
        formula_doc="MTBF = SUM(operating_hours) / SUM(failure_count)",
        compute=lambda df: (
            _require_cols(df, "operating_hours", "failure_count")
            or _div(df["operating_hours"].sum(), max(df["failure_count"].sum(), 1))
        ),
    )
)

_register(
    KPIDefinition(
        name="mean_time_to_repair",
        description="Average time to complete a repair after an unplanned failure.",
        unit="hr",
        formula_doc="MTTR = SUM(repair_time_hours) / SUM(failure_count)",
        compute=lambda df: (
            _require_cols(df, "repair_time_hours", "failure_count")
            or _div(df["repair_time_hours"].sum(), max(df["failure_count"].sum(), 1))
        ),
    )
)

_register(
    KPIDefinition(
        name="unplanned_downtime_rate",
        description="Percentage of scheduled hours lost to unplanned failures.",
        unit="%",
        formula_doc="downtime_rate = (downtime_hours / scheduled_hours) × 100",
        compute=lambda df: (
            _require_cols(df, "downtime_hours", "scheduled_hours")
            or _div(df["downtime_hours"].sum(), df["scheduled_hours"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="planned_maintenance_compliance",
        description="Percentage of planned maintenance tasks completed on schedule.",
        unit="%",
        formula_doc="PM_compliance = (pm_completed / pm_scheduled) × 100",
        compute=lambda df: (
            _require_cols(df, "pm_completed", "pm_scheduled")
            or _div(df["pm_completed"].sum(), df["pm_scheduled"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="maintenance_compliance",
        description="Percentage of all scheduled maintenance services completed on time.",
        unit="%",
        formula_doc="compliance = (on_time_services / total_scheduled_services) × 100",
        compute=lambda df: (
            _require_cols(df, "on_time_services", "total_scheduled_services")
            or _div(
                df["on_time_services"].sum(), df["total_scheduled_services"].sum(), 100
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="oil_sample_compliance",
        description="Percentage of scheduled oil samples collected on time.",
        unit="%",
        formula_doc="oil_compliance = (oil_samples_taken / oil_samples_due) × 100",
        compute=lambda df: (
            _require_cols(df, "oil_samples_taken", "oil_samples_due")
            or _div(df["oil_samples_taken"].sum(), df["oil_samples_due"].sum(), 100)
        ),
    )
)


# ===========================================================================
# 3. Safety & Fatigue Management
# ===========================================================================

_register(
    KPIDefinition(
        name="fatigue_event_rate",
        description="Number of detected fatigue events per 1,000 operating hours.",
        unit="events/1000hr",
        formula_doc="fatigue_rate = (fatigue_events / operating_hours) × 1000",
        compute=lambda df: (
            _require_cols(df, "fatigue_events", "operating_hours")
            or _div(df["fatigue_events"].sum(), df["operating_hours"].sum(), 1000)
        ),
    )
)

_register(
    KPIDefinition(
        name="speeding_rate",
        description="Speed-limit violations per 100 km driven.",
        unit="violations/100km",
        formula_doc="speeding_rate = (speed_violations / distance_km) × 100",
        compute=lambda df: (
            _require_cols(df, "speed_violations", "distance_km")
            or _div(df["speed_violations"].sum(), df["distance_km"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="seatbelt_compliance",
        description="Percentage of engine-on time the operator was wearing a seatbelt.",
        unit="%",
        formula_doc="seatbelt_compliance = (seatbelt_compliant_time / engine_on_hours) × 100",
        compute=lambda df: (
            _require_cols(df, "seatbelt_compliant_time", "engine_on_hours")
            or _div(
                df["seatbelt_compliant_time"].sum(), df["engine_on_hours"].sum(), 100
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="near_miss_rate",
        description="Near-miss incidents per 1,000 operating hours.",
        unit="events/1000hr",
        formula_doc="near_miss_rate = (near_misses / operating_hours) × 1000",
        compute=lambda df: (
            _require_cols(df, "near_misses", "operating_hours")
            or _div(df["near_misses"].sum(), df["operating_hours"].sum(), 1000)
        ),
    )
)

_register(
    KPIDefinition(
        name="unsafe_behaviour_rate",
        description="Unsafe behaviour events per 1,000 operating hours.",
        unit="events/1000hr",
        formula_doc="unsafe_rate = (unsafe_events / operating_hours) × 1000",
        compute=lambda df: (
            _require_cols(df, "unsafe_events", "operating_hours")
            or _div(df["unsafe_events"].sum(), df["operating_hours"].sum(), 1000)
        ),
    )
)


# ===========================================================================
# 4. Environmental Monitoring
# ===========================================================================

_register(
    KPIDefinition(
        name="co2_per_km",
        description="Average CO₂ emissions per kilometre driven.",
        unit="g/km",
        formula_doc="co2_per_km = SUM(co2_grams) / SUM(distance_km)",
        compute=lambda df: (
            _require_cols(df, "co2_grams", "distance_km")
            or _div(df["co2_grams"].sum(), df["distance_km"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="carbon_intensity",
        description="CO₂ emissions per tonne of material moved.",
        unit="kg CO₂/t",
        formula_doc="carbon_intensity = SUM(co2_grams) / (SUM(tonnes_moved) × 1000)",
        compute=lambda df: (
            _require_cols(df, "co2_grams", "tonnes_moved")
            or _div(df["co2_grams"].sum() / 1000, df["tonnes_moved"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="dust_compliance_rate",
        description="Percentage of dust readings within the regulatory threshold.",
        unit="%",
        formula_doc="dust_compliance = ((dust_readings - dust_threshold_breaches) / dust_readings) × 100",
        compute=lambda df: (
            _require_cols(df, "dust_readings", "dust_threshold_breaches")
            or _div(
                df["dust_readings"].sum() - df["dust_threshold_breaches"].sum(),
                df["dust_readings"].sum(),
                100,
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="water_intensity",
        description="Water consumed per tonne of material moved.",
        unit="L/t",
        formula_doc="water_intensity = SUM(water_litres) / SUM(tonnes_moved)",
        compute=lambda df: (
            _require_cols(df, "water_litres", "tonnes_moved")
            or _div(df["water_litres"].sum(), df["tonnes_moved"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="idle_emission_contribution",
        description="Percentage of total CO₂ emissions attributable to engine idling.",
        unit="%",
        formula_doc="idle_emission_pct = (idle_co2_grams / co2_grams) × 100",
        compute=lambda df: (
            _require_cols(df, "idle_co2_grams", "co2_grams")
            or _div(df["idle_co2_grams"].sum(), df["co2_grams"].sum(), 100)
        ),
    )
)


# ===========================================================================
# 5. Load & Tonnage Tracking
# ===========================================================================

_register(
    KPIDefinition(
        name="total_tonnes_moved",
        description="Total material moved across all haul events in the period.",
        unit="t",
        formula_doc="total_tonnes = SUM(payload_tonnes)",
        compute=lambda df: (
            _require_cols(df, "payload_tonnes")
            or round(float(df["payload_tonnes"].sum()), 1)
        ),
    )
)

_register(
    KPIDefinition(
        name="tonnes_per_hour",
        description="Material moved per operating hour — primary production rate KPI.",
        unit="t/hr",
        formula_doc="tph = SUM(payload_tonnes) / SUM(operating_hours)",
        compute=lambda df: (
            _require_cols(df, "payload_tonnes", "operating_hours")
            or _div(df["payload_tonnes"].sum(), df["operating_hours"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="tonnes_per_litre",
        description="Tonnes moved per litre of fuel — fuel productivity metric.",
        unit="t/L",
        formula_doc="t_per_litre = SUM(payload_tonnes) / SUM(fuel_litres)",
        compute=lambda df: (
            _require_cols(df, "payload_tonnes", "fuel_litres")
            or _div(df["payload_tonnes"].sum(), df["fuel_litres"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="overload_rate",
        description="Percentage of loads that exceed the maximum design payload.",
        unit="%",
        formula_doc="overload_rate = (overloaded_loads / load_count) × 100",
        compute=lambda df: (
            _require_cols(df, "overloaded_loads", "load_count")
            or _div(df["overloaded_loads"].sum(), df["load_count"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="payload_accuracy",
        description="Mean absolute deviation of actual payload from target, as a percentage.",
        unit="%",
        formula_doc="payload_accuracy = MEAN(|payload_tonnes - target_payload_tonnes| / target_payload_tonnes) × 100",
        compute=lambda df: (
            _require_cols(df, "payload_tonnes", "target_payload_tonnes")
            or round(
                float(
                    (
                        (df["payload_tonnes"] - df["target_payload_tonnes"]).abs()
                        / df["target_payload_tonnes"].replace(0, float("nan"))
                    ).mean()
                    * 100
                ),
                1,
            )
        ),
    )
)


# ===========================================================================
# 6. GPS / Pit Navigation
# ===========================================================================

_register(
    KPIDefinition(
        name="mean_haul_distance",
        description="Average round-trip haul distance per cycle.",
        unit="km",
        formula_doc="mean_haul_distance = SUM(haul_distance_km) / SUM(trip_count)",
        compute=lambda df: (
            _require_cols(df, "haul_distance_km", "trip_count")
            or _div(df["haul_distance_km"].sum(), df["trip_count"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="speed_compliance_rate",
        description="Percentage of operating time within the posted speed limit.",
        unit="%",
        formula_doc="speed_compliance = ((engine_on_hours - speeding_hours) / engine_on_hours) × 100",
        compute=lambda df: (
            _require_cols(df, "engine_on_hours", "speeding_hours")
            or _div(
                df["engine_on_hours"].sum() - df["speeding_hours"].sum(),
                df["engine_on_hours"].sum(),
                100,
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="route_deviation_rate",
        description="Route deviation events per 100 trips.",
        unit="events/100 trips",
        formula_doc="deviation_rate = (route_deviations / trip_count) × 100",
        compute=lambda df: (
            _require_cols(df, "route_deviations", "trip_count")
            or _div(df["route_deviations"].sum(), df["trip_count"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="geofence_violation_rate",
        description="Geofence boundary violations per 100 trips.",
        unit="events/100 trips",
        formula_doc="geofence_rate = (geofence_violations / trip_count) × 100",
        compute=lambda df: (
            _require_cols(df, "geofence_violations", "trip_count")
            or _div(df["geofence_violations"].sum(), df["trip_count"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="queue_time_ratio",
        description="Time spent queuing at load/dump points as a percentage of total cycle time.",
        unit="%",
        formula_doc="queue_ratio = (SUM(queue_time_min) / SUM(cycle_time_min)) × 100",
        compute=lambda df: (
            _require_cols(df, "queue_time_min", "cycle_time_min")
            or _div(df["queue_time_min"].sum(), df["cycle_time_min"].sum(), 100)
        ),
    )
)


# ===========================================================================
# 7. Compliance & Reporting
# ===========================================================================

_register(
    KPIDefinition(
        name="pre_shift_inspection_rate",
        description="Percentage of shifts where a pre-shift vehicle inspection was completed.",
        unit="%",
        formula_doc="inspection_rate = (pre_shift_checks_done / pre_shift_checks_due) × 100",
        compute=lambda df: (
            _require_cols(df, "pre_shift_checks_done", "pre_shift_checks_due")
            or _div(
                df["pre_shift_checks_done"].sum(), df["pre_shift_checks_due"].sum(), 100
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="license_compliance_rate",
        description="Percentage of active operators with a current, valid operating licence.",
        unit="%",
        formula_doc="license_compliance = (operators_licensed / operators_total) × 100",
        compute=lambda df: (
            _require_cols(df, "operators_licensed", "operators_total")
            or _div(df["operators_licensed"].sum(), df["operators_total"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="training_completion_rate",
        description="Percentage of mandatory training modules completed by the workforce.",
        unit="%",
        formula_doc="training_rate = (training_completed_count / training_required_count) × 100",
        compute=lambda df: (
            _require_cols(df, "training_completed_count", "training_required_count")
            or _div(
                df["training_completed_count"].sum(),
                df["training_required_count"].sum(),
                100,
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="incident_reporting_rate",
        description="Percentage of incidents that were formally reported (vs estimated total).",
        unit="%",
        formula_doc="reporting_rate = (incidents_reported / incidents_total) × 100",
        compute=lambda df: (
            _require_cols(df, "incidents_reported", "incidents_total")
            or _div(df["incidents_reported"].sum(), df["incidents_total"].sum(), 100)
        ),
    )
)


# ===========================================================================
# 8. Maximo Integration
# ===========================================================================

_register(
    KPIDefinition(
        name="work_order_backlog_ratio",
        description="Open work-order backlog as a ratio of the target backlog level.",
        unit="ratio",
        formula_doc="backlog_ratio = SUM(open_work_orders) / SUM(target_work_orders)",
        compute=lambda df: (
            _require_cols(df, "open_work_orders", "target_work_orders")
            or _div(df["open_work_orders"].sum(), df["target_work_orders"].sum())
        ),
    )
)

_register(
    KPIDefinition(
        name="pm_schedule_adherence",
        description="Percentage of preventive maintenance tasks completed within the scheduled window.",
        unit="%",
        formula_doc="pm_adherence = (pm_completed / pm_scheduled) × 100",
        compute=lambda df: (
            _require_cols(df, "pm_completed", "pm_scheduled")
            or _div(df["pm_completed"].sum(), df["pm_scheduled"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="defect_capture_rate",
        description="Percentage of logged defects that were converted to work orders.",
        unit="%",
        formula_doc="defect_capture = (work_orders_created / defects_raised) × 100",
        compute=lambda df: (
            _require_cols(df, "work_orders_created", "defects_raised")
            or _div(df["work_orders_created"].sum(), df["defects_raised"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="parts_availability_rate",
        description="Percentage of required spare parts available in stock at time of need.",
        unit="%",
        formula_doc="parts_availability = (parts_available / parts_required) × 100",
        compute=lambda df: (
            _require_cols(df, "parts_available", "parts_required")
            or _div(df["parts_available"].sum(), df["parts_required"].sum(), 100)
        ),
    )
)


# ===========================================================================
# 9. AI Assistant
# ===========================================================================

_register(
    KPIDefinition(
        name="prediction_accuracy",
        description="Percentage of ML model predictions that matched the observed outcome.",
        unit="%",
        formula_doc="prediction_accuracy = (correct_predictions / total_predictions) × 100",
        compute=lambda df: (
            _require_cols(df, "correct_predictions", "total_predictions")
            or _div(df["correct_predictions"].sum(), df["total_predictions"].sum(), 100)
        ),
    )
)

_register(
    KPIDefinition(
        name="anomaly_detection_precision",
        description="Percentage of flagged anomalies that were confirmed as genuine.",
        unit="%",
        formula_doc="precision = (anomalies_confirmed / anomalies_detected) × 100",
        compute=lambda df: (
            _require_cols(df, "anomalies_confirmed", "anomalies_detected")
            or _div(
                df["anomalies_confirmed"].sum(), df["anomalies_detected"].sum(), 100
            )
        ),
    )
)

_register(
    KPIDefinition(
        name="recommendation_adoption_rate",
        description="Percentage of AI-generated recommendations acted upon by operators.",
        unit="%",
        formula_doc="adoption_rate = (recommendations_adopted / recommendations_total) × 100",
        compute=lambda df: (
            _require_cols(df, "recommendations_adopted", "recommendations_total")
            or _div(
                df["recommendations_adopted"].sum(),
                df["recommendations_total"].sum(),
                100,
            )
        ),
    )
)


# ===========================================================================
# Utility functions
# ===========================================================================


def get_required_variables(kpi_name: str) -> list[str]:
    """Return the variable names required to compute a KPI.

    Works by running the KPI on an empty DataFrame — _require_cols raises
    ValueError listing every missing column, which we parse out.
    """
    import ast

    kpi = KPI_REGISTRY.get(kpi_name)
    if not kpi:
        return []
    try:
        kpi.compute(pd.DataFrame())
        return []
    except ValueError as exc:
        msg = str(exc)
        if "Required columns missing" in msg:
            try:
                return ast.literal_eval(msg.split(": ", 1)[1])
            except Exception:
                return []
    except Exception:
        pass
    return []


def get_all_required_variables() -> dict[str, list[str]]:
    """Return required variable names for every registered KPI."""
    return {name: get_required_variables(name) for name in KPI_REGISTRY}


def list_kpis() -> list[dict]:
    """Return a structured catalogue of all KPIs for the LLM."""
    return [
        {
            "name": k.name,
            "description": k.description,
            "unit": k.unit,
            "formula": k.formula_doc,
        }
        for k in KPI_REGISTRY.values()
    ]
