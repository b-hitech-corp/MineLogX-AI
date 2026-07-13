"""
Unit tests for FolderPipeline's agent/safety-net wiring (agent/pipeline.py).

Unlike test_pipeline.py (real Bedrock, real Strands agent, marked
@pytest.mark.integration), these tests mock _FileAnalysisAgent and every
impure tool call (csv_loader, schema_advisor, kpi_engine, stats_analyzer,
insight_extractor) so they run fast, offline, and deterministically. They
verify the contract that matters for the ingestion pipeline: the report
shape never regresses below what a fully deterministic run would produce,
regardless of what the agent does or doesn't call.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_analysis_agent.agent import pipeline as pipeline_module
from data_analysis_agent.agent.pipeline import FolderPipeline

FILE_PATH = "C1/fuel.csv"

FAKE_RAW_SCHEMA = {
    "file_path": FILE_PATH,
    "row_count": 100,
    "column_count": 4,
    "columns": [
        {"name": "vehicle_id", "type": "string", "null_pct": 0},
        {"name": "event_date", "type": "datetime", "null_pct": 0},
        {"name": "fuel_litres", "type": "float", "null_pct": 0},
        {"name": "idle_minutes", "type": "float", "null_pct": 0},
    ],
    "preview_rows": [],
}

FAKE_ADVISOR = {
    "file_path": FILE_PATH,
    "row_count": 100,
    "entity_columns": ["vehicle_id"],
    "datetime_columns": ["event_date"],
    "metric_columns": ["fuel_litres", "idle_minutes"],
    "categorical_columns": [],
    "column_mapping": {"fuel_litres": "fuel_litres"},
    "direct_kpi_mapping": {},
    "feasible_kpis": ["fuel_efficiency"],
    "infeasible_kpis": [],
    "timestamp_pairs": [],
    "recommended_analyses": [],
    "summary": "fake summary",
}

FAKE_KPI_RAW = {
    "kpis": {"fuel_efficiency": {"value": 4.2, "unit": "km/L"}},
    "errors": None,
    "metadata": [],
    "row_count_used": 100,
    "filter_applied": None,
    "grouped_by": None,
}

FAKE_STATS_RAW = {
    "statistics": {"fuel_litres": {"mean": 10.0}, "idle_minutes": {"mean": 3.0}},
    "columns_analyzed": ["fuel_litres", "idle_minutes"],
}

FAKE_RANKING_RAW = {
    "ranking": [{"rank": 1, "vehicle_id": "V1", "fuel_litres": 5.0}],
    "metric": "fuel_litres",
    "entity": "vehicle_id",
    "aggregation": "mean",
    "direction": "descending",
}

FAKE_TS_RAW = {
    "series": [{"date": "2026-01-01", "fuel_litres": 5.0, "idle_minutes": 1.0}],
    "freq": "W",
    "agg_func": "sum",
    "date_column": "event_date",
    "value_columns": ["fuel_litres", "idle_minutes"],
}

FAKE_OUTLIER_RAW = {
    "outlier_count": 1,
    "total_rows": 100,
    "outlier_pct": 1.0,
    "lower_bound": 0.0,
    "upper_bound": 10.0,
    "method": "iqr",
    "threshold": 1.5,
    "outlier_samples": [],
}

FAKE_TREND_RAW = {
    "value_column": "fuel_litres",
    "periods": 4,
    "freq": "W",
    "slope_per_period": 0.5,
    "r_squared": 0.8,
    "pct_change_first_to_last": 10.0,
    "direction": "increasing",
    "period_values": [],
}

FAKE_PERFORMANCE_RAW = {
    "metric": "fuel_litres",
    "entity": "vehicle_id",
    "top_performers": [],
    "bottom_performers": [],
    "fleet_average": 5.0,
    "fleet_std": 1.0,
}


class _NoOpAgent:
    """Simulates an agent that captured nothing this run."""

    def run(self, advisor):
        pass


class _PartialAgent:
    """Simulates an agent that covered KPIs, one chart, and one outlier column
    (fuel_litres) but left idle_minutes and everything else uncovered."""

    def run(self, advisor):
        pipeline_module._capture["kpi_raw"] = FAKE_KPI_RAW
        pipeline_module._capture["outliers"].append(
            {
                "column": "fuel_litres",
                "method": "iqr",
                "outlier_count": 2,
                "samples": [],
            }
        )
        pipeline_module._capture["charts"].append(
            {
                "chart_type": "KPICards",
                "library": "custom",
                "title": "Agent-built KPIs",
                "cards": [{"label": "Fuel Efficiency", "value": 4.2, "unit": "km/L"}],
            }
        )


def _patch_impure_tools(monkeypatch):
    monkeypatch.setattr(
        pipeline_module.csv_loader, "load_csv", lambda *a, **k: FAKE_RAW_SCHEMA
    )
    monkeypatch.setattr(
        pipeline_module.schema_advisor, "discover_schema", lambda *a, **k: FAKE_ADVISOR
    )
    monkeypatch.setattr(
        pipeline_module.kpi_engine, "calculate_kpi", lambda *a, **k: FAKE_KPI_RAW
    )
    monkeypatch.setattr(
        pipeline_module.stats_analyzer,
        "describe_columns",
        lambda *a, **k: FAKE_STATS_RAW,
    )
    monkeypatch.setattr(
        pipeline_module.stats_analyzer,
        "rank_entities",
        lambda *a, **k: FAKE_RANKING_RAW,
    )
    monkeypatch.setattr(
        pipeline_module.stats_analyzer,
        "time_series_aggregation",
        lambda *a, **k: FAKE_TS_RAW,
    )
    monkeypatch.setattr(
        pipeline_module.insight_extractor,
        "detect_outliers",
        lambda *a, **k: FAKE_OUTLIER_RAW,
    )
    monkeypatch.setattr(
        pipeline_module.insight_extractor,
        "detect_trend",
        lambda *a, **k: FAKE_TREND_RAW,
    )
    monkeypatch.setattr(
        pipeline_module.insight_extractor,
        "fleet_performance_summary",
        lambda *a, **k: FAKE_PERFORMANCE_RAW,
    )


def test_full_fallback_when_agent_captures_nothing(monkeypatch):
    """No agent coverage at all → every category falls back to the deterministic
    calls, exactly matching what the pre-Strands fixed pipeline always did."""
    _patch_impure_tools(monkeypatch)
    monkeypatch.setattr(pipeline_module, "_FileAnalysisAgent", _NoOpAgent)

    pipeline = FolderPipeline(local_mode=True)
    ctx = pipeline._process_file(FILE_PATH)

    assert ctx["status"] == "success"
    assert ctx["kpis"]["feasible"] == [
        {"name": "fuel_efficiency", "value": 4.2, "unit": "km/L"}
    ]
    assert ctx["statistics"] == FAKE_STATS_RAW["statistics"]

    outlier_cols = {o["column"] for o in ctx["insights"]["outliers"]}
    assert outlier_cols == {"fuel_litres", "idle_minutes"}
    for o in ctx["insights"]["outliers"]:
        assert o["outlier_count"] == 1  # from FAKE_OUTLIER_RAW

    trend_cols = {t["value_column"] for t in ctx["insights"]["trends"]}
    assert trend_cols == {"fuel_litres", "idle_minutes"}  # metric_cols[:2] == both
    for t in ctx["insights"]["trends"]:
        assert t["slope"] == 0.5  # confirms slope_per_period -> "slope" mapping fix

    assert ctx["insights"]["performance_summary"] == FAKE_PERFORMANCE_RAW

    # Charts fell back to the deterministic KPI-cards + bar + line build.
    chart_types = {c["chart_type"] for c in ctx["charts"]}
    assert chart_types == {"KPICards", "BarChart", "LineChart"}


def test_agent_coverage_is_kept_and_gaps_are_filled(monkeypatch):
    """Agent covers KPIs + one chart + the fuel_litres outlier only -> those
    exact values survive untouched, and only the uncovered categories
    (idle_minutes outlier, ranking/time-series/trends/performance summary)
    fall back."""
    _patch_impure_tools(monkeypatch)
    monkeypatch.setattr(pipeline_module, "_FileAnalysisAgent", _PartialAgent)

    pipeline = FolderPipeline(local_mode=True)
    ctx = pipeline._process_file(FILE_PATH)

    assert ctx["status"] == "success"

    outliers_by_col = {o["column"]: o for o in ctx["insights"]["outliers"]}
    assert set(outliers_by_col) == {"fuel_litres", "idle_minutes"}
    assert outliers_by_col["fuel_litres"]["outlier_count"] == 2  # agent's value, kept
    assert outliers_by_col["idle_minutes"]["outlier_count"] == 1  # fallback value

    # Agent's chart is used as-is; the deterministic fallback never runs
    # because ctx["charts"] was already non-empty.
    assert len(ctx["charts"]) == 1
    assert ctx["charts"][0]["title"] == "Agent-built KPIs"

    # Trends were never touched by the (partial) agent, so both fall back.
    trend_cols = {t["value_column"] for t in ctx["insights"]["trends"]}
    assert trend_cols == {"fuel_litres", "idle_minutes"}


def test_agent_exception_is_captured_not_raised(monkeypatch):
    """A raising agent shouldn't crash the pipeline — it should be recorded as
    an error and the safety net should still produce a full report."""
    _patch_impure_tools(monkeypatch)

    class _RaisingAgent:
        def run(self, advisor):
            raise RuntimeError("boom")

    monkeypatch.setattr(pipeline_module, "_FileAnalysisAgent", _RaisingAgent)

    pipeline = FolderPipeline(local_mode=True)
    ctx = pipeline._process_file(FILE_PATH)

    assert "agent" in ctx["errors"]
    assert ctx["status"] == "partial"
    # Safety net still ran despite the agent raising.
    assert ctx["kpis"]["feasible"] == [
        {"name": "fuel_efficiency", "value": 4.2, "unit": "km/L"}
    ]
    assert len(ctx["charts"]) >= 1
