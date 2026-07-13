"""
pipeline.py — Folder Analysis Pipeline

Processes every CSV in an S3 folder through the same fixed set of analytics
categories every run: KPIs, statistics, ranking, time series, outliers,
trends, performance summary, and charts. Schema discovery and dashboard
assembly stay deterministic Python; the analysis itself (which columns,
thresholds, and chart pairings to use) is driven by a Strands agent on
Bedrock (_FileAnalysisAgent, below) with a Python safety net that fills in
any mandatory category the agent skips — so coverage never regresses below
what a fully deterministic run would produce.

Called both offline by agent/ingest_orchestrator.py (populates the
analysis_vecs OpenSearch index) and live by the /analyze route in
backend/lambdas/api/handler.py (feeds the dashboard when a user selects a
Client in the UI).

Usage
-----
    from data_analysis_agent.agent.pipeline import FolderPipeline

    pipeline = FolderPipeline()                # S3 mode (IAM credentials)
    pipeline = FolderPipeline(local_mode=True) # local sample_data/ mode

    report = pipeline.run("C1")                         # returns dict
    report = pipeline.run("C1", output_path="out.json") # also writes JSON

The report schema is documented in _DASHBOARD_REPORT_SCHEMA at the bottom of
this file and is designed to be consumed directly by the front-end dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from strands.types.agent import Limits

from data_analysis_agent.agent.prompts import (
    FILE_ANALYSIS_SYSTEM_PROMPT,
    build_file_analysis_prompt,
)
from data_analysis_agent.config.settings import settings
from data_analysis_agent.tools import (
    chart_spec_builder,
    csv_loader,
    insight_extractor,
    kpi_engine,
    schema_advisor,
    stats_analyzer,
)
from data_analysis_agent.tools.s3_browser import list_folder

logger = logging.getLogger(__name__)

_LABEL = lambda col: col.replace("_", " ").title()  # noqa: E731


# ---------------------------------------------------------------------------
# Dashboard section definitions
# ---------------------------------------------------------------------------

# Maps each UI section → the KPI names that belong to it.
_SECTION_KPIS: dict[str, list[str]] = {
    "fleet": [
        "fleet_availability",
        "vehicle_utilization",
        "mean_cycle_time",
        "idle_rate",
        "on_time_delivery",
    ],
    "maintenance": [
        "mean_time_between_failures",
        "mean_time_to_repair",
        "unplanned_downtime_rate",
        "planned_maintenance_compliance",
        "maintenance_compliance",
        "oil_sample_compliance",
        "work_order_backlog_ratio",
        "pm_schedule_adherence",
        "defect_capture_rate",
        "parts_availability_rate",
    ],
    "kpis": [
        "pre_shift_inspection_rate",
        "license_compliance_rate",
        "training_completion_rate",
        "incident_reporting_rate",
        "prediction_accuracy",
        "anomaly_detection_precision",
        "recommendation_adoption_rate",
    ],
    "load_and_tonnage": [
        "total_tonnes_moved",
        "tonnes_per_hour",
        "haul_truck_productivity",
        "payload_utilization",
        "tonnes_per_litre",
        "overload_rate",
        "payload_accuracy",
    ],
    "fuel": [
        "fuel_efficiency",
        "fuel_consumption_rate",
        "cost_per_km",
        "co2_per_km",
        "carbon_intensity",
        "dust_compliance_rate",
        "water_intensity",
        "idle_emission_contribution",
    ],
    "gps_location": [
        "mean_haul_distance",
        "speed_compliance_rate",
        "route_deviation_rate",
        "geofence_violation_rate",
        "queue_time_ratio",
    ],
    "safety": [
        "fatigue_event_rate",
        "speeding_rate",
        "seatbelt_compliance",
        "near_miss_rate",
        "unsafe_behaviour_rate",
    ],
}

# Reverse lookup: kpi_name → section
_KPI_TO_SECTION: dict[str, str] = {
    kpi: section for section, kpis in _SECTION_KPIS.items() for kpi in kpis
}

# Column name keyword fragments used to route stats/outliers/trends to sections.
_SECTION_COL_KEYWORDS: dict[str, list[str]] = {
    "fleet": ["cycle", "util", "avail", "idle", "active", "scheduled", "dispatch"],
    "maintenance": [
        "maint",
        "repair",
        "downtime",
        "failure",
        "pm_",
        "service",
        "mtbf",
        "mttr",
    ],
    "kpis": ["compli", "inspect", "train", "licen"],
    "load_and_tonnage": ["tonn", "payload", "haul_truck"],
    "fuel": ["fuel", "litr", "emis", "co2", "carbon", "water", "dust"],
    "gps_location": ["dist", "speed", "route", "geofence", "queue", "trip", "gps"],
    "safety": [
        "fatigue",
        "safety",
        "seatbelt",
        "near_miss",
        "unsafe",
        "incident",
        "tire",
    ],
}


def _col_section(col_name: str) -> str | None:
    """Return which dashboard section a column belongs to, or None if unclassified."""
    col_lower = col_name.lower()
    for section, keywords in _SECTION_COL_KEYWORDS.items():
        if any(kw in col_lower for kw in keywords):
            return section
    return None


# ---------------------------------------------------------------------------
# Per-file Strands agent — tool wrappers + capture state
#
# One _FileAnalysisAgent runs per already-loaded CSV file (schema already
# discovered by _process_file before the agent starts). file_path/column
# mapping never need to be tool parameters — they're bound via _capture,
# which _reset_capture() replaces before each per-file run. _process_file's
# calls are sequential (a plain list comprehension in FolderPipeline.run()),
# never concurrent, so this module-level capture is safe — the same pattern
# agent/orchestrator.py already uses for its `_run_charts` list.
# ---------------------------------------------------------------------------

_capture: dict = {}


def _reset_capture(
    file_path: str, column_mapping: dict, direct_kpi_mapping: dict
) -> None:
    global _capture
    _capture = {
        "file_path": file_path,
        "column_mapping": column_mapping,
        "direct_kpi_mapping": direct_kpi_mapping,
        "kpi_raw": None,
        "stats_raw": None,
        "ranking_raw": None,
        "ts_raw": None,
        "outliers": [],
        "trends": [],
        "performance_summary": None,
        "charts": [],
    }


@tool
def kpi_engine__calculate_kpi(
    kpi_names: list[str],
    group_by: Optional[str] = None,
    filter_expr: Optional[str] = None,
) -> dict:
    """
    Calculate one or more KPIs for the current file using pre-defined formulas.
    Pass kpi_names=['*'] to compute every feasible KPI. Column name mapping is
    applied automatically from the discovered schema.
    """
    result = kpi_engine.calculate_kpi(
        _capture["file_path"],
        kpi_names,
        group_by=group_by,
        filter_expr=filter_expr,
        column_mapping=_capture["column_mapping"],
        direct_kpi_mapping=_capture["direct_kpi_mapping"],
    )
    _capture["kpi_raw"] = result
    return result


@tool
def kpi_engine__available_kpis() -> dict:
    """Return the catalogue of available KPI formulas."""
    return kpi_engine.available_kpis()


@tool
def stats_analyzer__describe_columns(columns: Optional[list[str]] = None) -> dict:
    """Descriptive statistics (mean, std, percentiles, skewness) for numeric columns."""
    result = stats_analyzer.describe_columns(_capture["file_path"], columns=columns)
    _capture["stats_raw"] = result
    return result


@tool
def stats_analyzer__rank_entities(
    metric_column: str,
    entity_column: str,
    top_n: int = 10,
    ascending: bool = False,
    agg_func: str = "mean",
) -> dict:
    """Rank entities (vehicles, drivers, routes) by a metric column."""
    result = stats_analyzer.rank_entities(
        _capture["file_path"],
        metric_column,
        entity_column,
        top_n=top_n,
        ascending=ascending,
        agg_func=agg_func,
    )
    _capture["ranking_raw"] = result
    return result


@tool
def stats_analyzer__time_series_aggregation(
    date_column: str,
    value_columns: list[str],
    freq: str = "W",
    agg_func: str = "sum",
    group_by: Optional[str] = None,
) -> dict:
    """Aggregate numeric columns over time. freq options: D, W, ME, QE."""
    result = stats_analyzer.time_series_aggregation(
        _capture["file_path"],
        date_column,
        value_columns,
        freq=freq,
        agg_func=agg_func,
        group_by=group_by,
    )
    _capture["ts_raw"] = result
    return result


@tool
def stats_analyzer__correlation_matrix(columns: Optional[list[str]] = None) -> dict:
    """
    Pearson correlation matrix for numeric columns — useful for deciding which
    metric pairs are worth ranking or trending together. Not a report section
    on its own.
    """
    return stats_analyzer.correlation_matrix(_capture["file_path"], columns=columns)


@tool
def insight_extractor__detect_outliers(
    column: str,
    method: str = "iqr",
    threshold: float = 1.5,
    entity_column: Optional[str] = None,
) -> dict:
    """Detect statistical outliers in a numeric column. method: iqr or zscore."""
    result = insight_extractor.detect_outliers(
        _capture["file_path"],
        column,
        method=method,
        threshold=threshold,
        entity_column=entity_column,
    )
    if "error" not in result:
        _capture["outliers"].append(
            {
                "column": column,
                "method": result.get("method", method),
                "outlier_count": result.get("outlier_count", 0),
                "samples": (result.get("outlier_samples") or [])[:5],
            }
        )
    return result


@tool
def insight_extractor__detect_trend(
    date_column: str,
    value_column: str,
    freq: str = "W",
) -> dict:
    """Fit a linear trend to a time-aggregated series; classify as increasing/decreasing/stable."""
    result = insight_extractor.detect_trend(
        _capture["file_path"], date_column, value_column, freq=freq
    )
    if "error" not in result:
        _capture["trends"].append(
            {
                "date_column": date_column,
                "value_column": value_column,
                "direction": result.get("direction"),
                "r_squared": result.get("r_squared"),
                "slope": result.get("slope_per_period"),
            }
        )
    return result


@tool
def insight_extractor__check_thresholds(rules: list[dict]) -> dict:
    """
    Check rule-based thresholds and return breaching rows. Each rule:
    {column, operator (>, <, >=, <=, ==), value, label (optional)}. Use this to
    inform which columns are worth flagging as outliers — not a report section
    on its own.
    """
    return insight_extractor.check_thresholds(_capture["file_path"], rules)


@tool
def insight_extractor__fleet_performance_summary(
    metric_column: str,
    entity_column: str,
    top_n: int = 5,
) -> dict:
    """Return top and bottom N performers for a metric. Good for executive summaries."""
    result = insight_extractor.fleet_performance_summary(
        _capture["file_path"], metric_column, entity_column, top_n=top_n
    )
    if "error" not in result:
        _capture["performance_summary"] = result
    return result


@tool
def chart_spec_builder__build_line_chart(
    title: str,
    data: list[dict],
    x_key: str,
    y_keys: list[str],
    y_label: Optional[str] = None,
    x_label: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Build a Recharts-compatible JSON spec for a line/time-series chart."""
    spec = chart_spec_builder.build_line_chart(
        title=title,
        data=data,
        x_key=x_key,
        y_keys=y_keys,
        y_label=y_label,
        x_label=x_label,
        description=description,
    )
    _capture["charts"].append(spec)
    return spec


@tool
def chart_spec_builder__build_bar_chart(
    title: str,
    data: list[dict],
    x_key: str,
    y_keys: list[str],
    layout: str = "vertical",
    stacked: bool = False,
    y_label: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Build a Recharts-compatible JSON spec for a bar chart. layout: vertical or horizontal."""
    spec = chart_spec_builder.build_bar_chart(
        title=title,
        data=data,
        x_key=x_key,
        y_keys=y_keys,
        layout=layout,
        stacked=stacked,
        y_label=y_label,
        description=description,
    )
    _capture["charts"].append(spec)
    return spec


@tool
def chart_spec_builder__build_kpi_cards(
    title: str,
    kpis: list[dict],
    description: Optional[str] = None,
) -> dict:
    """Build a KPI summary card layout spec. Each kpi: {label, value, unit, trend}."""
    spec = chart_spec_builder.build_kpi_cards(
        title=title, kpis=kpis, description=description
    )
    _capture["charts"].append(spec)
    return spec


@tool
def chart_spec_builder__build_pie_chart(
    title: str,
    data: list[dict],
    name_key: str = "name",
    value_key: str = "value",
    donut: bool = True,
    description: Optional[str] = None,
) -> dict:
    """Build a Recharts-compatible JSON spec for a pie or donut chart."""
    spec = chart_spec_builder.build_pie_chart(
        title=title,
        data=data,
        name_key=name_key,
        value_key=value_key,
        donut=donut,
        description=description,
    )
    _capture["charts"].append(spec)
    return spec


@tool
def chart_spec_builder__chart_from_time_series(
    title: str,
    description: Optional[str] = None,
) -> dict:
    """Build a line chart directly from the most recent time_series_aggregation result."""
    if _capture["ts_raw"] is None:
        return {"error": "Call stats_analyzer__time_series_aggregation first."}
    spec = chart_spec_builder.chart_from_time_series(
        _capture["ts_raw"], title=title, description=description
    )
    _capture["charts"].append(spec)
    return spec


_FILE_ANALYSIS_TOOLS = [
    kpi_engine__available_kpis,
    kpi_engine__calculate_kpi,
    stats_analyzer__describe_columns,
    stats_analyzer__rank_entities,
    stats_analyzer__time_series_aggregation,
    stats_analyzer__correlation_matrix,
    insight_extractor__detect_outliers,
    insight_extractor__detect_trend,
    insight_extractor__check_thresholds,
    insight_extractor__fleet_performance_summary,
    chart_spec_builder__build_line_chart,
    chart_spec_builder__build_bar_chart,
    chart_spec_builder__build_kpi_cards,
    chart_spec_builder__build_pie_chart,
    chart_spec_builder__chart_from_time_series,
]


class _FileAnalysisAgent:
    """
    Strands + Bedrock agent that analyses one already-loaded CSV file.

    Covers the same categories FolderPipeline has always covered (KPIs,
    statistics, ranking, time series, outliers, trends, performance summary,
    charts), but lets Claude choose which columns/thresholds/chart pairings
    matter most instead of always taking the first N columns. Its tool calls
    are captured into the module-level `_capture` dict as they happen, so the
    report shape stays fully Python-controlled regardless of what the model
    says in its final text response.
    """

    def __init__(self) -> None:
        self._model = BedrockModel(
            model_id=settings.bedrock.model_id,
            region_name=settings.bedrock.region,
        )

    def run(self, advisor: dict) -> None:
        agent = Agent(
            model=self._model,
            tools=_FILE_ANALYSIS_TOOLS,
            system_prompt=FILE_ANALYSIS_SYSTEM_PROMPT,
        )
        agent(
            build_file_analysis_prompt(advisor),
            limits=Limits(turns=settings.bedrock.max_agent_turns),
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class FolderPipeline:
    """
    Runs every CSV in a folder through the same fixed analytics categories and
    returns a single structured JSON-serialisable report.

    Steps per file
    --------------
    1  load_csv           — fetch & parse, build DataFrame cache (deterministic)
    2  discover_schema    — classify columns, assess KPI feasibility (deterministic —
                             dashboard section-routing depends on exact column names)
    3–9 _FileAnalysisAgent — a Strands agent on Bedrock covers KPIs, statistics,
                             ranking, time series, outliers, trends, and performance
                             summary, choosing columns/thresholds itself instead of
                             always taking the first N; a Python safety net fills in
                             any mandatory category the agent skipped
    10 build_charts       — uses whatever charts the agent built; falls back to the
                             deterministic KPI-cards + bar + line charts only if it
                             built none
    """

    MAX_METRIC_COLS = 5
    TOP_N = 10
    TREND_FREQ = "W"

    def __init__(self, local_mode: bool = False, backend: str = "bedrock") -> None:
        self.local_mode = local_mode
        self.backend = backend
        self._file_agent: Optional[_FileAnalysisAgent] = None

    def _agent(self) -> _FileAnalysisAgent:
        if self._file_agent is None:
            self._file_agent = _FileAnalysisAgent()
        return self._file_agent

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, folder: str, *, output_path: Optional[str] = None) -> dict:
        """
        Process all CSVs in *folder* and return a dashboard-structured report.

        The output is organised by UI section (overview, fleet, maintenance,
        kpis, load_and_tonnage, fuel, gps_location, safety) rather than by
        file, making it ready for direct consumption by the front-end.

        Parameters
        ----------
        folder      : S3 prefix or local subfolder name (e.g. "C1")
        output_path : optional file path; if given, the report is also
                      written as a pretty-printed JSON file
        """
        files = list_folder(folder, local_mode=self.local_mode)
        logger.info("Found %d CSV file(s) in '%s'", len(files), folder)

        files_data = [self._process_file(fp) for fp in files]

        report: dict = {
            "folder": folder,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(files),
            **self._build_dashboard(files_data),
        }

        if output_path:
            Path(output_path).write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8"
            )
            logger.info("Report written → %s", output_path)

        return report

    # ------------------------------------------------------------------
    # Per-file orchestration
    # ------------------------------------------------------------------

    def _process_file(self, file_path: str) -> dict:
        logger.info("  → %s", file_path)

        ctx: dict = {
            "file_path": file_path,
            "status": "pending",
            "schema": None,
            "kpis": {"feasible": [], "infeasible": []},
            "statistics": None,
            "insights": {
                "outliers": [],
                "trends": [],
                "performance_summary": None,
            },
            "charts": [],
            "errors": {},
        }

        # ── Step 1: Load ──────────────────────────────────────────────
        raw_schema = self._call(
            ctx,
            "load_csv",
            csv_loader.load_csv,
            file_path,
            use_local_fallback=self.local_mode,
        )
        if raw_schema is None:
            ctx["status"] = "error"
            return ctx

        # ── Step 2: Schema discovery ──────────────────────────────────
        advisor = self._call(
            ctx,
            "discover_schema",
            schema_advisor.discover_schema,
            file_path,
            backend=self.backend,
        )
        if advisor is None:
            ctx["status"] = "error"
            return ctx

        entity_cols = advisor["entity_columns"]
        dt_cols = advisor["datetime_columns"]
        metric_cols = advisor["metric_columns"][: self.MAX_METRIC_COLS]
        feasible_kpis = advisor["feasible_kpis"]
        column_mapping = advisor.get("column_mapping", {})
        direct_kpi_mapping = advisor.get("direct_kpi_mapping", {})

        ctx["schema"] = {
            "row_count": raw_schema["row_count"],
            "column_count": raw_schema["column_count"],
            "entity_columns": entity_cols,
            "datetime_columns": dt_cols,
            "metric_columns": metric_cols,
            "categorical_columns": advisor["categorical_columns"],
            "timestamp_pairs": advisor["timestamp_pairs"],
            "column_mapping": column_mapping,
            "direct_kpi_mapping": direct_kpi_mapping,
            "data_quality": self._data_quality(raw_schema),
            "preview_rows": raw_schema.get("preview_rows", []),
        }

        # ── Steps 3–9: agent-driven analysis ───────────────────────────
        # A Strands agent covers KPIs, statistics, ranking, time series,
        # outliers, trends, and performance summary, choosing columns and
        # thresholds itself. Its tool calls land directly in the module-level
        # _capture dict (see _reset_capture/_FILE_ANALYSIS_TOOLS above), so
        # nothing here depends on the LLM formatting a report correctly.
        _reset_capture(file_path, column_mapping, direct_kpi_mapping)
        try:
            self._agent().run(advisor)
        except Exception as exc:
            ctx["errors"]["agent"] = str(exc)
            logger.exception("  ✗ file-analysis agent raised: %s", exc)

        # KPIs — fall back only if the agent skipped a feasible calculation.
        kpi_raw = _capture["kpi_raw"]
        if kpi_raw is None and feasible_kpis:
            kpi_raw = self._call(
                ctx,
                "kpi_calculation",
                kpi_engine.calculate_kpi,
                file_path,
                feasible_kpis,
                column_mapping=column_mapping,
                direct_kpi_mapping=direct_kpi_mapping,
            )
        ctx["kpis"] = {
            "feasible": self._format_kpi_results(kpi_raw, feasible_kpis),
            "infeasible": advisor["infeasible_kpis"],
        }

        # Statistics — fall back only if the agent never described any columns.
        stats_raw = _capture["stats_raw"]
        if stats_raw is None and metric_cols:
            stats_raw = self._call(
                ctx,
                "statistics",
                stats_analyzer.describe_columns,
                file_path,
                metric_cols,
            )
        ctx["statistics"] = stats_raw.get("statistics") if stats_raw else None

        # Ranking — only feeds the fallback bar chart below; no ctx field of its own.
        ranking_raw = _capture["ranking_raw"]
        if ranking_raw is None and metric_cols and entity_cols:
            ranking_raw = self._call(
                ctx,
                "ranking",
                stats_analyzer.rank_entities,
                file_path,
                metric_cols[0],
                entity_cols[0],
                top_n=self.TOP_N,
            )

        # Time series — only feeds the fallback line chart below; no ctx field of its own.
        ts_raw = _capture["ts_raw"]
        if ts_raw is None and dt_cols and metric_cols:
            ts_raw = self._call(
                ctx,
                "time_series",
                stats_analyzer.time_series_aggregation,
                file_path,
                dt_cols[0],
                metric_cols[:2],
                freq=self.TREND_FREQ,
            )

        # Outliers — keep every agent-found outlier, then fill in any metric
        # column the agent never checked.
        entity_col = entity_cols[0] if entity_cols else None
        ctx["insights"]["outliers"] = list(_capture["outliers"])
        covered_outlier_cols = {o["column"] for o in _capture["outliers"]}
        for col in metric_cols:
            if col in covered_outlier_cols:
                continue
            result = self._call(
                ctx,
                f"outliers_{col}",
                insight_extractor.detect_outliers,
                file_path,
                col,
                entity_column=entity_col,
            )
            if result:
                ctx["insights"]["outliers"].append(
                    {
                        "column": col,
                        "method": result.get("method", "iqr"),
                        "outlier_count": result.get("outlier_count", 0),
                        "samples": (result.get("outlier_samples") or [])[:5],
                    }
                )

        # Trends — same keep-then-fill-gaps pattern, scoped to metric_cols[:2]
        # like the original fixed pipeline.
        ctx["insights"]["trends"] = list(_capture["trends"])
        covered_trend_cols = {t["value_column"] for t in _capture["trends"]}
        if dt_cols:
            for col in metric_cols[:2]:
                if col in covered_trend_cols:
                    continue
                result = self._call(
                    ctx,
                    f"trend_{col}",
                    insight_extractor.detect_trend,
                    file_path,
                    dt_cols[0],
                    col,
                    freq=self.TREND_FREQ,
                )
                if result:
                    ctx["insights"]["trends"].append(
                        {
                            "date_column": dt_cols[0],
                            "value_column": col,
                            "direction": result.get("direction"),
                            "r_squared": result.get("r_squared"),
                            "slope": result.get("slope_per_period"),
                        }
                    )

        # Performance summary — fall back only if the agent never built one.
        perf = _capture["performance_summary"]
        if perf is None and metric_cols and entity_col:
            perf = self._call(
                ctx,
                "performance_summary",
                insight_extractor.fleet_performance_summary,
                file_path,
                metric_cols[0],
                entity_col,
                top_n=5,
            )
        ctx["insights"]["performance_summary"] = perf

        # ── Step 10: Charts — use the agent's; fall back only if it built none ──
        ctx["charts"] = list(_capture["charts"])
        if not ctx["charts"]:
            ctx["charts"] = self._build_charts(
                kpi_raw,
                ranking_raw,
                ts_raw,
                entity_cols,
                metric_cols,
                dt_cols,
            )

        ctx["status"] = "success" if not ctx["errors"] else "partial"
        return ctx

    # ------------------------------------------------------------------
    # Dashboard assembly — reorganises per-file data into UI sections
    # ------------------------------------------------------------------

    def _build_dashboard(self, files_data: list[dict]) -> dict:
        """
        Aggregate per-file analytics into the eight dashboard sections.

        Routing rules
        -------------
        KPIs       → section defined in _KPI_TO_SECTION
        Statistics ) → section inferred from column name via _col_section()
        Outliers   )
        Trends     )
        Charts     → section tag added by _build_charts(); fallback via content
        """
        # Initialise empty section buckets
        sections: dict[str, dict] = {
            name: {
                "kpis": [],
                "statistics": {},
                "outliers": [],
                "trends": [],
                "charts": [],
            }
            for name in _SECTION_KPIS
        }

        overview: dict = {
            "total_rows": 0,
            "files": [],
            "data_quality": [],
            "kpi_summary": {},
        }

        for fd in files_data:
            schema = fd.get("schema") or {}

            # ── Overview accumulation ─────────────────────────────────
            overview["total_rows"] += schema.get("row_count") or 0
            overview["files"].append(
                {
                    "path": fd["file_path"],
                    "status": fd["status"],
                    "rows": schema.get("row_count"),
                    "columns": schema.get("column_count"),
                    "errors": fd.get("errors") or {},
                }
            )
            overview["data_quality"].extend(schema.get("data_quality") or [])

            # ── KPIs → section (deduplicated by name — last computed wins) ──
            for kpi in (fd.get("kpis") or {}).get("feasible") or []:
                name = kpi.get("name", "")
                section = _KPI_TO_SECTION.get(name)
                if not section:
                    continue
                existing = next(
                    (
                        i
                        for i, k in enumerate(sections[section]["kpis"])
                        if k.get("name") == name
                    ),
                    None,
                )
                if existing is None:
                    sections[section]["kpis"].append(kpi)
                elif kpi.get("status") != "error":
                    sections[section]["kpis"][existing] = kpi

            # ── Statistics → section (by column name) ─────────────────
            for col, stats in (fd.get("statistics") or {}).items():
                section = _col_section(col)
                if section:
                    sections[section]["statistics"][col] = stats

            # ── Outliers → section ────────────────────────────────────
            for o in (fd.get("insights") or {}).get("outliers") or []:
                section = _col_section(o.get("column", ""))
                if section:
                    sections[section]["outliers"].append(o)

            # ── Trends → section ──────────────────────────────────────
            for t in (fd.get("insights") or {}).get("trends") or []:
                section = _col_section(t.get("value_column", ""))
                if section:
                    sections[section]["trends"].append(t)

            # ── Charts → section (uses tag added by _build_charts) ────
            for chart in fd.get("charts") or []:
                section = chart.get("section") or self._chart_section(chart)
                target = section if section in sections else None
                if target:
                    sections[target]["charts"].append(chart)

        # ── KPI summary for overview ──────────────────────────────────
        overview["kpi_summary"] = {
            "total_computed": sum(len(s["kpis"]) for s in sections.values()),
            "by_section": {name: len(s["kpis"]) for name, s in sections.items()},
        }

        return {"overview": overview, **sections}

    def _chart_section(self, chart: dict) -> str | None:
        """Infer a chart's section from its content when no tag is present."""
        chart_type = chart.get("type", "")

        if chart_type == "KPICards":
            for card in chart.get("kpis") or []:
                # Convert prettified label back to snake_case for lookup
                snake = card.get("label", "").lower().replace(" ", "_")
                section = _KPI_TO_SECTION.get(snake)
                if section:
                    return section

        elif chart_type in ("BarChart", "LineChart"):
            for key in (chart.get("y_keys") or []) + [chart.get("x_key", "")]:
                section = _col_section(key)
                if section:
                    return section
            for s in chart.get("series") or []:
                section = _col_section(s.get("key", ""))
                if section:
                    return section

        return None

    # ------------------------------------------------------------------
    # Chart assembly — always calls builder functions, never describes
    # ------------------------------------------------------------------

    def _build_charts(
        self,
        kpi_raw: Optional[dict],
        ranking_raw: Optional[dict],
        ts_raw: Optional[dict],
        entity_cols: list[str],
        metric_cols: list[str],
        dt_cols: list[str],
    ) -> list[dict]:
        charts: list[dict] = []

        # KPI cards — one card per computed KPI, grouped by section
        if kpi_raw:
            # Build one KPICards chart per section so each section gets its own card block
            by_section: dict[str, list] = {}
            for name, data in kpi_raw.get("kpis", {}).items():
                if "value" not in data:
                    continue
                section = _KPI_TO_SECTION.get(name, "kpis")
                by_section.setdefault(section, []).append(
                    {
                        "label": _LABEL(name),
                        "value": data["value"],
                        "unit": data.get("unit", ""),
                    }
                )
            for section, cards in by_section.items():
                chart = chart_spec_builder.build_kpi_cards(
                    title=f"{_LABEL(section)} KPIs",
                    kpis=cards,
                    description=f"Aggregated KPIs for the {section.replace('_', ' ')} section.",
                )
                chart["section"] = section
                charts.append(chart)

        # Bar chart from ranking — tagged with the primary metric's section
        if ranking_raw and entity_cols and metric_cols:
            ranking_data = ranking_raw.get("ranking", [])
            if ranking_data:
                chart = chart_spec_builder.build_bar_chart(
                    title=f"Top {len(ranking_data)} {_LABEL(entity_cols[0])} by {_LABEL(metric_cols[0])}",
                    data=ranking_data,
                    x_key=entity_cols[0],
                    y_keys=[metric_cols[0]],
                    y_label=_LABEL(metric_cols[0]),
                    description=f"Ranked by {metric_cols[0]} (mean, descending).",
                )
                chart["section"] = _col_section(metric_cols[0]) or "fleet"
                charts.append(chart)

        # Line chart from time series — tagged with the primary metric's section
        if ts_raw and dt_cols and metric_cols:
            series = ts_raw.get("series", [])
            if series:
                chart = chart_spec_builder.chart_from_time_series(
                    ts_raw,
                    title=f"{_LABEL(metric_cols[0])} Over Time ({self.TREND_FREQ})",
                    description=f"Weekly aggregation of {metric_cols[0]} by {dt_cols[0]}.",
                )
                chart["section"] = _col_section(metric_cols[0]) or "fleet"
                charts.append(chart)

        return charts

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call(self, ctx: dict, step: str, fn, *args, **kwargs) -> Any:
        """
        Call *fn* with *args/kwargs*, capturing any exception into ctx["errors"].
        Returns None on failure so the pipeline can continue gracefully.
        """
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and "error" in result:
                ctx["errors"][step] = result["error"]
                logger.warning("  ✗ %s → %s", step, result["error"])
                return None
            return result
        except Exception as exc:
            ctx["errors"][step] = str(exc)
            logger.exception("  ✗ %s raised: %s", step, exc)
            return None

    @staticmethod
    def _data_quality(raw_schema: dict) -> list[dict]:
        """Return columns with >10 % null values."""
        return [
            {"column": c["name"], "null_pct": c["null_pct"]}
            for c in raw_schema.get("columns", [])
            if c.get("null_pct", 0) > 10
        ]

    @staticmethod
    def _format_kpi_results(kpi_raw: Optional[dict], names: list[str]) -> list[dict]:
        if not kpi_raw:
            return [{"name": n, "status": "not_computed"} for n in names]

        out: list[dict] = []
        for name, data in kpi_raw.get("kpis", {}).items():
            if "value" in data:
                out.append(
                    {"name": name, "value": data["value"], "unit": data.get("unit", "")}
                )
            elif "by_group" in data:
                out.append(
                    {
                        "name": name,
                        "grouped_by": data.get("group_column"),
                        "by_group": data["by_group"],
                        "unit": data.get("unit", ""),
                    }
                )

        for name, err in (kpi_raw.get("errors") or {}).items():
            out.append({"name": name, "status": "error", "error": err})

        return out


# ---------------------------------------------------------------------------
# Report schema reference
# ---------------------------------------------------------------------------
_DASHBOARD_REPORT_SCHEMA = {
    "folder": "str",
    "processed_at": "str  — ISO-8601 UTC timestamp",
    "file_count": "int",
    "overview": {
        "total_rows": "int  — sum across all files",
        "files": "list[{path, status, rows, columns, errors}]",
        "data_quality": "list[{column, null_pct}]  — cols >10% nulls across all files",
        "kpi_summary": {
            "total_computed": "int",
            "by_section": "dict[section → int]",
        },
    },
    # Each section below follows the same shape:
    "<section>": {
        "kpis": "list[{name, value, unit}]  — KPIs belonging to this section",
        "statistics": "dict[col → {mean, std, min, 25%, 50%, 75%, max}]",
        "outliers": "list[{column, method, outlier_count, samples}]",
        "trends": "list[{date_column, value_column, direction, r_squared, slope}]",
        "charts": "list[chart_spec]  — section='<section>' tag on every chart",
    },
    # Sections: fleet | maintenance | kpis | load_and_tonnage | fuel | gps_location | safety
}
