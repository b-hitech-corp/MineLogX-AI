"""
KPI formula registry for fleet management.

Each KPI is defined as a pure function that receives a pandas DataFrame
and returns a scalar or dict. The LLM never computes these values —
it only decides which KPI to request and how to present the result.

Formula documentation is included so the LLM can describe methodology
accurately to end users.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class KPIDefinition:
    name: str
    description: str
    unit: str
    formula_doc: str                        # human-readable formula explanation
    compute: Callable[[pd.DataFrame], Any]  # deterministic function


# ---------------------------------------------------------------------------
# Helper validators
# ---------------------------------------------------------------------------

def _require_cols(df: pd.DataFrame, *cols: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Required columns missing from dataset: {missing}")


# ---------------------------------------------------------------------------
# KPI Definitions
# ---------------------------------------------------------------------------

KPI_REGISTRY: dict[str, KPIDefinition] = {}


def _register(kpi: KPIDefinition) -> KPIDefinition:
    KPI_REGISTRY[kpi.name] = kpi
    return kpi


_register(KPIDefinition(
    name="fuel_efficiency",
    description="Average distance travelled per unit of fuel consumed across the fleet.",
    unit="km/L",
    formula_doc="fuel_efficiency = SUM(distance_km) / SUM(fuel_litres)",
    compute=lambda df: (
        _require_cols(df, "distance_km", "fuel_litres") or
        round(df["distance_km"].sum() / df["fuel_litres"].sum(), 2)
    ),
))

_register(KPIDefinition(
    name="vehicle_utilization",
    description="Percentage of scheduled hours that vehicles were actively in use.",
    unit="%",
    formula_doc="utilization = (active_hours / scheduled_hours) x 100",
    compute=lambda df: (
        _require_cols(df, "active_hours", "scheduled_hours") or
        round((df["active_hours"].sum() / df["scheduled_hours"].sum()) * 100, 1)
    ),
))

_register(KPIDefinition(
    name="cost_per_km",
    description="Total operating cost divided by total distance driven.",
    unit="currency/km",
    formula_doc="cost_per_km = SUM(total_cost) / SUM(distance_km)",
    compute=lambda df: (
        _require_cols(df, "total_cost", "distance_km") or
        round(df["total_cost"].sum() / df["distance_km"].sum(), 3)
    ),
))

_register(KPIDefinition(
    name="maintenance_compliance",
    description="Percentage of scheduled maintenance tasks completed on time.",
    unit="%",
    formula_doc="compliance = (on_time_services / total_scheduled_services) x 100",
    compute=lambda df: (
        _require_cols(df, "on_time_services", "total_scheduled_services") or
        round(
            (df["on_time_services"].sum() / df["total_scheduled_services"].sum()) * 100,
            1
        )
    ),
))

_register(KPIDefinition(
    name="mean_time_between_failures",
    description="Average operating hours between unplanned breakdowns.",
    unit="hours",
    formula_doc="MTBF = total_operating_hours / number_of_failures",
    compute=lambda df: (
        _require_cols(df, "operating_hours", "failure_count") or
        round(df["operating_hours"].sum() / max(df["failure_count"].sum(), 1), 1)
    ),
))

_register(KPIDefinition(
    name="idle_rate",
    description="Proportion of engine-on time spent stationary.",
    unit="%",
    formula_doc="idle_rate = (idle_hours / engine_on_hours) x 100",
    compute=lambda df: (
        _require_cols(df, "idle_hours", "engine_on_hours") or
        round((df["idle_hours"].sum() / df["engine_on_hours"].sum()) * 100, 1)
    ),
))

_register(KPIDefinition(
    name="on_time_delivery",
    description="Percentage of deliveries completed within the scheduled time window.",
    unit="%",
    formula_doc="OTD = (on_time_deliveries / total_deliveries) x 100",
    compute=lambda df: (
        _require_cols(df, "on_time_deliveries", "total_deliveries") or
        round((df["on_time_deliveries"].sum() / df["total_deliveries"].sum()) * 100, 1)
    ),
))

_register(KPIDefinition(
    name="co2_per_km",
    description="Average CO2 emissions per kilometre driven.",
    unit="g/km",
    formula_doc="co2_per_km = SUM(co2_grams) / SUM(distance_km)",
    compute=lambda df: (
        _require_cols(df, "co2_grams", "distance_km") or
        round(df["co2_grams"].sum() / df["distance_km"].sum(), 1)
    ),
))


def list_kpis() -> list[dict]:
    """Return a structured list of available KPIs for the LLM's tool description."""
    return [
        {
            "name": k.name,
            "description": k.description,
            "unit": k.unit,
            "formula": k.formula_doc,
        }
        for k in KPI_REGISTRY.values()
    ]
