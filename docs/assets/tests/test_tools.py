"""
Tests for all fleet agent tools.
Run with: pytest tests/test_tools.py -v

Column names are discovered from the loaded CSV schema at runtime so these
tests work regardless of which CSV file is placed in sample_data/.
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

FILE = "fleet_may_2024.csv"


# ---------------------------------------------------------------------------
# Schema discovery helpers
# ---------------------------------------------------------------------------

def _col_names(schema: dict) -> list[str]:
    return [c["name"] for c in schema["columns"]]


def _cols_of_type(schema: dict, *types: str) -> list[str]:
    return [c["name"] for c in schema["columns"] if c["type"] in types]


def _id_columns(schema: dict) -> list[str]:
    return [c["name"] for c in schema["columns"] if c["name"].endswith("_id")]


def _first_numeric(schema: dict) -> str | None:
    cols = _cols_of_type(schema, "float", "integer")
    return cols[0] if cols else None


def _first_datetime(schema: dict) -> str | None:
    cols = _cols_of_type(schema, "datetime")
    return cols[0] if cols else None


def _first_entity(schema: dict) -> str | None:
    id_cols = _id_columns(schema)
    if id_cols:
        return id_cols[0]
    cat_cols = _cols_of_type(schema, "categorical", "string")
    return cat_cols[0] if cat_cols else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schema() -> dict:
    """Load the CSV once per session; all tests share this schema description."""
    return load_csv(FILE, use_local_fallback=True)


@pytest.fixture(autouse=True)
def load_sample():
    """Ensure the DataFrame is in cache before every test."""
    load_csv(FILE, use_local_fallback=True)


# ---------------------------------------------------------------------------
# csv_loader
# ---------------------------------------------------------------------------

class TestCsvLoader:
    def test_schema_keys_present(self, schema):
        for key in ("file_path", "row_count", "column_count", "columns", "preview_rows"):
            assert key in schema, f"Missing key '{key}' in schema"

    def test_row_count_positive(self, schema):
        assert schema["row_count"] > 0

    def test_column_count_matches(self, schema):
        assert schema["column_count"] == len(schema["columns"])
        assert schema["column_count"] > 0

    def test_each_column_has_required_fields(self, schema):
        for col in schema["columns"]:
            assert "name" in col
            assert "type" in col
            assert "null_count" in col

    def test_at_least_one_id_column(self, schema):
        id_cols = _id_columns(schema)
        assert len(id_cols) >= 1, (
            f"Expected at least one *_id column. Found: {_col_names(schema)}"
        )

    def test_datetime_columns_auto_detected(self, schema):
        dt_cols = _cols_of_type(schema, "datetime")
        assert len(dt_cols) >= 1, (
            f"Expected at least one auto-detected datetime column. "
            f"Columns: {_col_names(schema)}"
        )

    def test_preview_rows_returned(self, schema):
        assert isinstance(schema["preview_rows"], list)
        assert len(schema["preview_rows"]) > 0

    def test_get_dataframe_cached(self):
        df = get_dataframe(FILE)
        assert len(df) > 0

    def test_explicit_date_columns_parsed(self, schema):
        dt_col = _first_datetime(schema)
        if dt_col is None:
            pytest.skip("No datetime column detected in this CSV")
        result = load_csv(FILE, date_columns=[dt_col], use_local_fallback=True)
        col_info = next(c for c in result["columns"] if c["name"] == dt_col)
        assert col_info["type"] == "datetime"

    def test_nonexistent_date_column_ignored(self, schema):
        """Passing a column name that does not exist must not raise."""
        result = load_csv(FILE, date_columns=["__nonexistent__"], use_local_fallback=True)
        assert result["row_count"] > 0


# ---------------------------------------------------------------------------
# kpi_engine
# ---------------------------------------------------------------------------

class TestKpiEngine:
    def test_available_kpis_returns_list(self):
        result = available_kpis()
        assert "available_kpis" in result
        assert len(result["available_kpis"]) >= 1

    def test_available_kpis_each_has_required_fields(self):
        for kpi in available_kpis()["available_kpis"]:
            assert "name" in kpi
            assert "description" in kpi
            assert "unit" in kpi

    def test_calculate_kpi_returns_valid_structure(self):
        kpi_name = available_kpis()["available_kpis"][0]["name"]
        result = calculate_kpi(FILE, [kpi_name])
        assert "kpis" in result
        assert "errors" in result
        # Either computed successfully or returned a graceful column-missing error
        in_kpis = kpi_name in result["kpis"]
        in_errors = result["errors"] and kpi_name in result["errors"]
        assert in_kpis or in_errors, f"KPI '{kpi_name}' missing from both kpis and errors"

    def test_calculate_all_kpis_no_crash(self):
        result = calculate_kpi(FILE, ["*"])
        assert "kpis" in result

    def test_grouped_kpi(self, schema):
        entity_col = _first_entity(schema)
        if entity_col is None:
            pytest.skip("No entity column found for grouping")
        kpi_name = available_kpis()["available_kpis"][0]["name"]
        result = calculate_kpi(FILE, [kpi_name], group_by=entity_col)
        assert "kpis" in result
        kpi_result = result["kpis"].get(kpi_name)
        if kpi_result:
            assert "by_group" in kpi_result

    def test_filtered_kpi(self, schema):
        cat_cols = _cols_of_type(schema, "categorical", "string")
        if not cat_cols:
            pytest.skip("No categorical column available for filter test")
        col = cat_cols[0]
        col_info = next(c for c in schema["columns"] if c["name"] == col)
        top_values = col_info.get("top_values", {})
        if not top_values:
            pytest.skip(f"No top_values available for column '{col}'")
        val = list(top_values.keys())[0]
        filter_expr = f"{col} == '{val}'"
        result = calculate_kpi(FILE, ["*"], filter_expr=filter_expr)
        assert result.get("filter_applied") == filter_expr

    def test_unknown_kpi_returns_error(self):
        result = calculate_kpi(FILE, ["nonexistent_kpi"])
        assert result["errors"] is not None
        assert "nonexistent_kpi" in result["errors"]


# ---------------------------------------------------------------------------
# stats_analyzer
# ---------------------------------------------------------------------------

class TestStatsAnalyzer:
    def test_describe_columns(self, schema):
        numeric_cols = _cols_of_type(schema, "float", "integer")
        if not numeric_cols:
            pytest.skip("No numeric columns in CSV")
        result = describe_columns(FILE, numeric_cols[:2])
        for col in numeric_cols[:2]:
            assert col in result["statistics"]
            assert "mean" in result["statistics"][col]

    def test_describe_all_numeric_columns(self, schema):
        numeric_cols = _cols_of_type(schema, "float", "integer")
        if not numeric_cols:
            pytest.skip("No numeric columns in CSV")
        result = describe_columns(FILE)
        assert "statistics" in result
        assert len(result["statistics"]) > 0

    def test_rank_entities(self, schema):
        numeric_cols = _cols_of_type(schema, "float", "integer")
        entity_col = _first_entity(schema)
        if not numeric_cols or not entity_col:
            pytest.skip("Need at least one numeric and one entity column")
        result = rank_entities(FILE, numeric_cols[0], entity_col, top_n=3)
        assert "ranking" in result
        assert len(result["ranking"]) <= 3
        if result["ranking"]:
            assert result["ranking"][0]["rank"] == 1

    def test_rank_entities_ascending(self, schema):
        numeric_cols = _cols_of_type(schema, "float", "integer")
        entity_col = _first_entity(schema)
        if not numeric_cols or not entity_col:
            pytest.skip("Need at least one numeric and one entity column")
        result = rank_entities(FILE, numeric_cols[0], entity_col, top_n=5, ascending=True)
        assert "ranking" in result

    def test_time_series(self, schema):
        dt_col = _first_datetime(schema)
        numeric_cols = _cols_of_type(schema, "float", "integer")
        if not dt_col or not numeric_cols:
            pytest.skip("Need a datetime column and a numeric column for time series")
        result = time_series_aggregation(FILE, dt_col, numeric_cols[:1], freq="W")
        assert "series" in result
        assert len(result["series"]) > 0


# ---------------------------------------------------------------------------
# insight_extractor
# ---------------------------------------------------------------------------

class TestInsightExtractor:
    def test_detect_outliers(self, schema):
        numeric_col = _first_numeric(schema)
        if numeric_col is None:
            pytest.skip("No numeric columns in CSV")
        entity_col = _first_entity(schema)
        result = detect_outliers(FILE, numeric_col, entity_column=entity_col)
        assert "outlier_count" in result
        assert "outlier_samples" in result

    def test_detect_trend(self, schema):
        dt_col = _first_datetime(schema)
        numeric_col = _first_numeric(schema)
        if not dt_col or not numeric_col:
            pytest.skip("Need a datetime column and a numeric column for trend detection")
        result = detect_trend(FILE, dt_col, numeric_col, freq="W")
        assert result["direction"] in ("increasing", "decreasing", "stable")
        assert "r_squared" in result

    def test_check_thresholds(self, schema):
        numeric_col = _first_numeric(schema)
        if numeric_col is None:
            pytest.skip("No numeric columns in CSV")
        col_info = next(c for c in schema["columns"] if c["name"] == numeric_col)
        threshold = col_info.get("mean") or col_info.get("min") or 0
        result = check_thresholds(FILE, [
            {"column": numeric_col, "operator": ">", "value": threshold, "label": "threshold_test"},
        ])
        assert result["rules_checked"] == 1
        assert result["threshold_findings"][0]["breach_count"] >= 0

    def test_performance_summary(self, schema):
        numeric_col = _first_numeric(schema)
        entity_col = _first_entity(schema)
        if not numeric_col or not entity_col:
            pytest.skip("Need a numeric column and an entity column")
        result = fleet_performance_summary(FILE, numeric_col, entity_col, top_n=3)
        assert "top_performers" in result
        assert "bottom_performers" in result


# ---------------------------------------------------------------------------
# chart_spec_builder
# ---------------------------------------------------------------------------

class TestChartSpecBuilder:
    def test_line_chart(self):
        spec = build_line_chart(
            title="Metric over time",
            data=[{"ts": "2024-05-01", "value": 210}],
            x_key="ts",
            y_keys=["value"],
        )
        assert spec["chart_type"] == "LineChart"
        assert spec["library"] == "recharts"
        assert spec["data"][0]["value"] == 210

    def test_bar_chart(self):
        spec = build_bar_chart(
            title="Value by entity",
            data=[{"entity_id": "E001", "value": 28.5}],
            x_key="entity_id",
            y_keys=["value"],
        )
        assert spec["chart_type"] == "BarChart"
        assert len(spec["series"]) == 1

    def test_bar_chart_stacked(self):
        spec = build_bar_chart(
            title="Stacked",
            data=[{"id": "A", "v1": 1, "v2": 2}],
            x_key="id",
            y_keys=["v1", "v2"],
            stacked=True,
        )
        assert all(s["stacked"] for s in spec["series"])

    def test_kpi_cards(self):
        spec = build_kpi_cards(
            title="Fleet KPIs",
            kpis=[{"label": "Cycle Time", "value": 42.1, "unit": "min", "trend": "down"}],
        )
        assert spec["chart_type"] == "KPICards"
        assert len(spec["cards"]) == 1

    def test_pie_chart(self):
        spec = build_pie_chart(
            title="Distribution",
            data=[{"name": "Zone A", "value": 5}, {"name": "Zone B", "value": 6}],
        )
        assert spec["chart_type"] == "PieChart"
        assert spec["donut"] is True
        assert len(spec["data"]) == 2

    def test_chart_series_colors_assigned(self):
        spec = build_line_chart(
            title="Multi-series",
            data=[{"t": "2024-05-01", "a": 1, "b": 2}],
            x_key="t",
            y_keys=["a", "b"],
        )
        assert len(spec["series"]) == 2
        assert all("color" in s for s in spec["series"])
