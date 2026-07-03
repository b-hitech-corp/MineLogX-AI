"""
pipeline.py — Deterministic Folder Analysis Pipeline

Processes every CSV in an S3 folder through a fixed, ordered sequence of
analytics steps. Chart specs are always built by calling chart_spec_builder
functions directly — the LLM is never asked to decide whether to call them.

Usage
-----
    from data_analysis_agent.agent.pipeline import FolderPipeline

    pipeline = FolderPipeline()                # S3 mode (IAM credentials)
    pipeline = FolderPipeline(local_mode=True) # local sample_data/ mode

    report = pipeline.run("C1")                         # returns dict
    report = pipeline.run("C1", output_path="out.json") # also writes JSON

The report schema is documented in _FILE_REPORT_SCHEMA at the bottom of
this file and is designed to be consumed directly by the front-end dashboard.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

_LABEL = lambda col: col.replace("_", " ").title()   # noqa: E731


# ---------------------------------------------------------------------------
# Dashboard section definitions
# ---------------------------------------------------------------------------

# Maps each UI section → the KPI names that belong to it.
_SECTION_KPIS: dict[str, list[str]] = {
    "fleet": [
        "fleet_availability", "vehicle_utilization", "mean_cycle_time",
        "idle_rate", "on_time_delivery",
    ],
    "maintenance": [
        "mean_time_between_failures", "mean_time_to_repair", "unplanned_downtime_rate",
        "planned_maintenance_compliance", "maintenance_compliance", "oil_sample_compliance",
        "work_order_backlog_ratio", "pm_schedule_adherence", "defect_capture_rate",
        "parts_availability_rate",
    ],
    "kpis": [
        "pre_shift_inspection_rate", "license_compliance_rate", "training_completion_rate",
        "incident_reporting_rate", "prediction_accuracy", "anomaly_detection_precision",
        "recommendation_adoption_rate",
    ],
    "load_and_tonnage": [
        "total_tonnes_moved", "tonnes_per_hour", "haul_truck_productivity",
        "payload_utilization", "tonnes_per_litre", "overload_rate", "payload_accuracy",
    ],
    "fuel": [
        "fuel_efficiency", "fuel_consumption_rate", "cost_per_km",
        "co2_per_km", "carbon_intensity", "dust_compliance_rate",
        "water_intensity", "idle_emission_contribution",
    ],
    "gps_location": [
        "mean_haul_distance", "speed_compliance_rate", "route_deviation_rate",
        "geofence_violation_rate", "queue_time_ratio",
    ],
    "safety": [
        "fatigue_event_rate", "speeding_rate", "seatbelt_compliance",
        "near_miss_rate", "unsafe_behaviour_rate",
    ],
}

# Reverse lookup: kpi_name → section
_KPI_TO_SECTION: dict[str, str] = {
    kpi: section
    for section, kpis in _SECTION_KPIS.items()
    for kpi in kpis
}

# Column name keyword fragments used to route stats/outliers/trends to sections.
_SECTION_COL_KEYWORDS: dict[str, list[str]] = {
    "fleet":          ["cycle", "util", "avail", "idle", "active", "scheduled", "dispatch"],
    "maintenance":    ["maint", "repair", "downtime", "failure", "pm_", "service", "mtbf", "mttr"],
    "kpis":           ["compli", "inspect", "train", "licen"],
    "load_and_tonnage": ["tonn", "payload", "haul_truck"],
    "fuel":           ["fuel", "litr", "emis", "co2", "carbon", "water", "dust"],
    "gps_location":   ["dist", "speed", "route", "geofence", "queue", "trip", "gps"],
    "safety":         ["fatigue", "safety", "seatbelt", "near_miss", "unsafe", "incident", "tire"],
}


def _col_section(col_name: str) -> str | None:
    """Return which dashboard section a column belongs to, or None if unclassified."""
    col_lower = col_name.lower()
    for section, keywords in _SECTION_COL_KEYWORDS.items():
        if any(kw in col_lower for kw in keywords):
            return section
    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class FolderPipeline:
    """
    Runs a fixed analytics sequence on every CSV in a folder and returns
    a single structured JSON-serialisable report.

    Steps per file
    --------------
    1  load_csv           — fetch & parse, build DataFrame cache
    2  discover_schema    — classify columns, assess KPI feasibility
    3  calculate_kpis     — all feasible KPIs
    4  describe_columns   — full descriptive statistics
    5  rank_entities      — top/bottom N by primary metric
    6  time_series        — weekly aggregation of primary metrics
    7  detect_outliers    — IQR outliers for each metric column
    8  detect_trends      — linear trend for each (datetime, metric) pair
    9  performance_summary— fleet top/bottom performers
    10 build_charts       — KPI cards + bar + line (always called, never described)
    """

    MAX_METRIC_COLS = 5
    TOP_N           = 10
    TREND_FREQ      = "W"

    def __init__(self, local_mode: bool = False, backend: str = "bedrock") -> None:
        self.local_mode = local_mode
        self.backend = backend

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
            "folder":       folder,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "file_count":   len(files),
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
            "status":    "pending",
            "schema":    None,
            "kpis":      {"feasible": [], "infeasible": []},
            "statistics": None,
            "insights":  {
                "outliers":            [],
                "trends":              [],
                "performance_summary": None,
            },
            "charts": [],
            "errors": {},
        }

        # ── Step 1: Load ──────────────────────────────────────────────
        raw_schema = self._call(ctx, "load_csv",
            csv_loader.load_csv,
            file_path,
            use_local_fallback=self.local_mode,
        )
        if raw_schema is None:
            ctx["status"] = "error"
            return ctx

        # ── Step 2: Schema discovery ──────────────────────────────────
        advisor = self._call(ctx, "discover_schema",
            schema_advisor.discover_schema,
            file_path,
            backend=self.backend,
        )
        if advisor is None:
            ctx["status"] = "error"
            return ctx

        entity_cols        = advisor["entity_columns"]
        dt_cols            = advisor["datetime_columns"]
        metric_cols        = advisor["metric_columns"][:self.MAX_METRIC_COLS]
        feasible_kpis      = advisor["feasible_kpis"]
        column_mapping     = advisor.get("column_mapping", {})
        direct_kpi_mapping = advisor.get("direct_kpi_mapping", {})

        ctx["schema"] = {
            "row_count":           raw_schema["row_count"],
            "column_count":        raw_schema["column_count"],
            "entity_columns":      entity_cols,
            "datetime_columns":    dt_cols,
            "metric_columns":      metric_cols,
            "categorical_columns": advisor["categorical_columns"],
            "timestamp_pairs":     advisor["timestamp_pairs"],
            "column_mapping":      column_mapping,
            "direct_kpi_mapping":  direct_kpi_mapping,
            "data_quality":        self._data_quality(raw_schema),
            "preview_rows":        raw_schema.get("preview_rows", []),
        }

        # ── Step 3: KPIs ──────────────────────────────────────────────
        kpi_raw = None
        if feasible_kpis:
            kpi_raw = self._call(ctx, "kpi_calculation",
                kpi_engine.calculate_kpi,
                file_path, feasible_kpis,
                column_mapping=column_mapping,
                direct_kpi_mapping=direct_kpi_mapping,
            )
        ctx["kpis"] = {
            "feasible":   self._format_kpi_results(kpi_raw, feasible_kpis),
            "infeasible": advisor["infeasible_kpis"],
        }

        # ── Step 4: Statistics ────────────────────────────────────────
        if metric_cols:
            stats_raw = self._call(ctx, "statistics",
                stats_analyzer.describe_columns,
                file_path, metric_cols,
            )
            ctx["statistics"] = stats_raw.get("statistics") if stats_raw else None

        # ── Step 5: Ranking ───────────────────────────────────────────
        ranking_raw = None
        if metric_cols and entity_cols:
            ranking_raw = self._call(ctx, "ranking",
                stats_analyzer.rank_entities,
                file_path,
                metric_cols[0],
                entity_cols[0],
                top_n=self.TOP_N,
            )

        # ── Step 6: Time series ───────────────────────────────────────
        ts_raw = None
        if dt_cols and metric_cols:
            ts_raw = self._call(ctx, "time_series",
                stats_analyzer.time_series_aggregation,
                file_path, dt_cols[0], metric_cols[:2],
                freq=self.TREND_FREQ,
            )

        # ── Step 7: Outliers ──────────────────────────────────────────
        entity_col = entity_cols[0] if entity_cols else None
        for col in metric_cols:
            result = self._call(ctx, f"outliers_{col}",
                insight_extractor.detect_outliers,
                file_path, col, entity_column=entity_col,
            )
            if result:
                ctx["insights"]["outliers"].append({
                    "column":        col,
                    "method":        result.get("method", "iqr"),
                    "outlier_count": result.get("outlier_count", 0),
                    "samples":       (result.get("outlier_samples") or [])[:5],
                })

        # ── Step 8: Trends ────────────────────────────────────────────
        if dt_cols:
            for col in metric_cols[:2]:
                result = self._call(ctx, f"trend_{col}",
                    insight_extractor.detect_trend,
                    file_path, dt_cols[0], col, freq=self.TREND_FREQ,
                )
                if result:
                    ctx["insights"]["trends"].append({
                        "date_column":  dt_cols[0],
                        "value_column": col,
                        "direction":    result.get("direction"),
                        "r_squared":    result.get("r_squared"),
                        "slope":        result.get("slope"),
                    })

        # ── Step 9: Performance summary ───────────────────────────────
        if metric_cols and entity_col:
            perf = self._call(ctx, "performance_summary",
                insight_extractor.fleet_performance_summary,
                file_path, metric_cols[0], entity_col, top_n=5,
            )
            ctx["insights"]["performance_summary"] = perf

        # ── Step 10: Charts (always built programmatically) ───────────
        ctx["charts"] = self._build_charts(
            kpi_raw, ranking_raw, ts_raw,
            entity_cols, metric_cols, dt_cols,
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
                "kpis":       [],
                "statistics": {},
                "outliers":   [],
                "trends":     [],
                "charts":     [],
            }
            for name in _SECTION_KPIS
        }

        overview: dict = {
            "total_rows":   0,
            "files":        [],
            "data_quality": [],
            "kpi_summary":  {},
        }

        for fd in files_data:
            schema = fd.get("schema") or {}

            # ── Overview accumulation ─────────────────────────────────
            overview["total_rows"] += schema.get("row_count") or 0
            overview["files"].append({
                "path":    fd["file_path"],
                "status":  fd["status"],
                "rows":    schema.get("row_count"),
                "columns": schema.get("column_count"),
                "errors":  fd.get("errors") or {},
            })
            overview["data_quality"].extend(schema.get("data_quality") or [])

            # ── KPIs → section (deduplicated by name — last computed wins) ──
            for kpi in (fd.get("kpis") or {}).get("feasible") or []:
                name    = kpi.get("name", "")
                section = _KPI_TO_SECTION.get(name)
                if not section:
                    continue
                existing = next(
                    (i for i, k in enumerate(sections[section]["kpis"]) if k.get("name") == name),
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
            "by_section":     {name: len(s["kpis"]) for name, s in sections.items()},
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
                    {"label": _LABEL(name), "value": data["value"], "unit": data.get("unit", "")}
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
                out.append({"name": name, "value": data["value"], "unit": data.get("unit", "")})
            elif "by_group" in data:
                out.append({"name": name, "grouped_by": data.get("group_column"), "by_group": data["by_group"], "unit": data.get("unit", "")})

        for name, err in (kpi_raw.get("errors") or {}).items():
            out.append({"name": name, "status": "error", "error": err})

        return out


# ---------------------------------------------------------------------------
# Report schema reference
# ---------------------------------------------------------------------------
_DASHBOARD_REPORT_SCHEMA = {
    "folder":       "str",
    "processed_at": "str  — ISO-8601 UTC timestamp",
    "file_count":   "int",
    "overview": {
        "total_rows":  "int  — sum across all files",
        "files":       "list[{path, status, rows, columns, errors}]",
        "data_quality": "list[{column, null_pct}]  — cols >10% nulls across all files",
        "kpi_summary": {
            "total_computed": "int",
            "by_section":     "dict[section → int]",
        },
    },
    # Each section below follows the same shape:
    "<section>": {
        "kpis":       "list[{name, value, unit}]  — KPIs belonging to this section",
        "statistics": "dict[col → {mean, std, min, 25%, 50%, 75%, max}]",
        "outliers":   "list[{column, method, outlier_count, samples}]",
        "trends":     "list[{date_column, value_column, direction, r_squared, slope}]",
        "charts":     "list[chart_spec]  — section='<section>' tag on every chart",
    },
    # Sections: fleet | maintenance | kpis | load_and_tonnage | fuel | gps_location | safety
}
