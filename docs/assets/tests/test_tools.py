"""
Tests for all fleet agent tools.
Run with: pytest tests/test_tools.py -v
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.csv_loader import load_csv, get_dataframe
from tools.kpi_engine import calculate_kpi, available_kpis
from tools.stats_analyzer import describe_columns, rank_entities, time_series_aggregation
from tools.insight_extractor import (
    detect_outliers, detect_trend, check_thresholds, fleet_performance_summary
)
from tools.chart_spec_builder import (
    build_line_chart, build_bar_chart, build_kpi_cards, build_pie_chart
)

FILE = "fleet_may_2024.csv" # To be defined later *** 


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def load_sample():
    """Load the sample CSV before every test."""
    load_csv(FILE, date_columns=["date"], use_local_fallback=True)


# ---------------------------------------------------------------------------
# csv_loader
# ---------------------------------------------------------------------------

class TestCsvLoader:
    def test_schema_returned(self):
        result = load_csv(FILE, use_local_fallback=True)
        assert result["row_count"] == 15
        assert result["column_count"] > 5
        assert any(c["name"] == "vehicle_id" for c in result["columns"])

    def test_datetime_detected(self):
        result = load_csv(FILE, date_columns=["date"], use_local_fallback=True)
        date_col = next(c for c in result["columns"] if c["name"] == "date")
        assert date_col["type"] == "datetime"

    def test_get_dataframe_cached(self):
        df = get_dataframe(FILE)
        assert len(df) == 15


# ---------------------------------------------------------------------------
# kpi_engine
# ---------------------------------------------------------------------------

class TestKpiEngine:
    def test_available_kpis(self):
        result = available_kpis()
        assert len(result["available_kpis"]) >= 5

    def test_fuel_efficiency(self):
        result = calculate_kpi(FILE, ["fuel_efficiency"])
        assert "fuel_efficiency" in result["kpis"]
        val = result["kpis"]["fuel_efficiency"]["value"]
        assert 5.0 < val < 15.0, f"Unexpected fuel efficiency: {val}"

    def test_grouped_kpi(self):
        result = calculate_kpi(FILE, ["fuel_efficiency"], group_by="vehicle_id")
        assert "by_group" in result["kpis"]["fuel_efficiency"]

    def test_filtered_kpi(self):
        result = calculate_kpi(FILE, ["fuel_efficiency"], filter_expr="region == 'North'")
        assert result["filter_applied"] == "region == 'North'"

    def test_unknown_kpi_returns_error(self):
        result = calculate_kpi(FILE, ["nonexistent_kpi"])
        assert result["errors"] is not None


# ---------------------------------------------------------------------------
# stats_analyzer
# ---------------------------------------------------------------------------

class TestStatsAnalyzer:
    def test_describe_columns(self):
        result = describe_columns(FILE, ["distance_km", "fuel_litres"])
        assert "distance_km" in result["statistics"]
        assert "mean" in result["statistics"]["distance_km"]

    def test_rank_entities(self):
        result = rank_entities(FILE, "fuel_litres", "vehicle_id", top_n=3)
        assert len(result["ranking"]) == 3
        assert result["ranking"][0]["rank"] == 1

    def test_time_series(self):
        result = time_series_aggregation(FILE, "date", ["distance_km"], freq="W")
        assert "series" in result
        assert len(result["series"]) > 0


# ---------------------------------------------------------------------------
# insight_extractor
# ---------------------------------------------------------------------------

class TestInsightExtractor:
    def test_detect_outliers(self):
        result = detect_outliers(FILE, "idle_hours", entity_column="vehicle_id")
        assert "outlier_count" in result
        assert "outlier_samples" in result

    def test_detect_trend(self):
        result = detect_trend(FILE, "date", "distance_km", freq="W")
        assert result["direction"] in ("increasing", "decreasing", "stable")
        assert "r_squared" in result

    def test_check_thresholds(self):
        result = check_thresholds(FILE, [
            {"column": "idle_hours", "operator": ">", "value": 2.0, "label": "High idle"},
        ])
        assert result["rules_checked"] == 1
        assert result["threshold_findings"][0]["breach_count"] >= 0

    def test_performance_summary(self):
        result = fleet_performance_summary(FILE, "fuel_litres", "vehicle_id", top_n=3)
        assert len(result["top_performers"]) == 3
        assert len(result["bottom_performers"]) == 3


# ---------------------------------------------------------------------------
# chart_spec_builder
# ---------------------------------------------------------------------------

class TestChartSpecBuilder:
    def test_line_chart(self):
        spec = build_line_chart(
            title="Distance over time",
            data=[{"date": "2024-05-01", "distance_km": 210}],
            x_key="date",
            y_keys=["distance_km"],
        )
        assert spec["chart_type"] == "LineChart"
        assert spec["library"] == "recharts"

    def test_bar_chart(self):
        spec = build_bar_chart(
            title="Fuel by vehicle",
            data=[{"vehicle_id": "V001", "fuel_litres": 28.5}],
            x_key="vehicle_id",
            y_keys=["fuel_litres"],
        )
        assert spec["chart_type"] == "BarChart"

    def test_kpi_cards(self):
        spec = build_kpi_cards(
            title="Fleet KPIs",
            kpis=[{"label": "Fuel Efficiency", "value": 7.8, "unit": "km/L", "trend": "up"}],
        )
        assert spec["chart_type"] == "KPICards"
        assert len(spec["cards"]) == 1

    def test_pie_chart(self):
        spec = build_pie_chart(
            title="Region distribution",
            data=[{"name": "North", "value": 5}, {"name": "South", "value": 6}],
        )
        assert spec["chart_type"] == "PieChart"
        assert spec["donut"] is True
