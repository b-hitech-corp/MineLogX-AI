"""
pipeline.py — Deterministic Folder Analysis Pipeline

Processes every CSV in an S3 folder through a fixed, ordered sequence of
analytics steps. Chart specs are always built by calling chart_spec_builder
functions directly — the LLM is never asked to decide whether to call them.

Usage
-----
    from agent.pipeline import FolderPipeline

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

from config.settings import settings
from tools import (
    chart_spec_builder,
    csv_loader,
    insight_extractor,
    kpi_engine,
    schema_advisor,
    stats_analyzer,
)
from tools.s3_browser import list_folder

logger = logging.getLogger(__name__)

_LABEL = lambda col: col.replace("_", " ").title()   # noqa: E731


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

    def __init__(self, local_mode: bool = False) -> None:
        self.local_mode = local_mode

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, folder: str, *, output_path: Optional[str] = None) -> dict:
        """
        Process all CSVs in *folder* and return a structured report dict.

        Parameters
        ----------
        folder      : S3 prefix or local subfolder name (e.g. "C1")
        output_path : optional file path; if given, the report is also
                      written as a pretty-printed JSON file
        """
        files = list_folder(folder, local_mode=self.local_mode)
        logger.info("Found %d CSV file(s) in '%s'", len(files), folder)

        report: dict = {
            "folder":       folder,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "file_count":   len(files),
            "files":        [self._process_file(fp) for fp in files],
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
        )
        if advisor is None:
            ctx["status"] = "error"
            return ctx

        entity_cols   = advisor["entity_columns"]
        dt_cols       = advisor["datetime_columns"]
        metric_cols   = advisor["metric_columns"][:self.MAX_METRIC_COLS]
        feasible_kpis = advisor["feasible_kpis"]

        ctx["schema"] = {
            "row_count":          raw_schema["row_count"],
            "column_count":       raw_schema["column_count"],
            "entity_columns":     entity_cols,
            "datetime_columns":   dt_cols,
            "metric_columns":     metric_cols,
            "categorical_columns": advisor["categorical_columns"],
            "timestamp_pairs":    advisor["timestamp_pairs"],
            "data_quality":       self._data_quality(raw_schema),
            "preview_rows":       raw_schema.get("preview_rows", []),
        }

        # ── Step 3: KPIs ──────────────────────────────────────────────
        kpi_raw = None
        if feasible_kpis:
            kpi_raw = self._call(ctx, "kpi_calculation",
                kpi_engine.calculate_kpi,
                file_path, feasible_kpis,
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

        # KPI cards
        if kpi_raw:
            cards = [
                {"label": _LABEL(name), "value": data["value"], "unit": data.get("unit", "")}
                for name, data in kpi_raw.get("kpis", {}).items()
                if "value" in data
            ]
            if cards:
                charts.append(chart_spec_builder.build_kpi_cards(
                    title="Key Performance Indicators",
                    kpis=cards,
                    description="Aggregated KPIs computed from the dataset.",
                ))

        # Bar chart from ranking
        if ranking_raw and entity_cols and metric_cols:
            ranking_data = ranking_raw.get("ranking", [])
            if ranking_data:
                charts.append(chart_spec_builder.build_bar_chart(
                    title=f"Top {len(ranking_data)} {_LABEL(entity_cols[0])} by {_LABEL(metric_cols[0])}",
                    data=ranking_data,
                    x_key=entity_cols[0],
                    y_keys=[metric_cols[0]],
                    y_label=_LABEL(metric_cols[0]),
                    description=f"Ranked by {metric_cols[0]} (mean, descending).",
                ))

        # Line chart from time series
        if ts_raw and dt_cols and metric_cols:
            series = ts_raw.get("series", [])
            if series:
                charts.append(chart_spec_builder.chart_from_time_series(
                    ts_raw,
                    title=f"{_LABEL(metric_cols[0])} Over Time ({self.TREND_FREQ})",
                    description=f"Weekly aggregation of {metric_cols[0]} by {dt_cols[0]}.",
                ))

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
_FILE_REPORT_SCHEMA = {
    "file_path":  "str  — S3 key relative to prefix, e.g. 'C1/events.csv'",
    "status":     "str  — 'success' | 'partial' | 'error'",
    "schema": {
        "row_count":           "int",
        "column_count":        "int",
        "entity_columns":      "list[str]",
        "datetime_columns":    "list[str]",
        "metric_columns":      "list[str]",
        "categorical_columns": "list[str]",
        "timestamp_pairs":     "list[{start, end}]",
        "data_quality":        "list[{column, null_pct}]  — only cols >10% nulls",
        "preview_rows":        "list[dict]  — first 3 rows",
    },
    "kpis": {
        "feasible":   "list[{name, value, unit}]",
        "infeasible": "list[{kpi, missing_columns}]",
    },
    "statistics":   "dict[col → {mean, std, min, 25%, 50%, 75%, max, skewness, kurtosis}]",
    "insights": {
        "outliers":            "list[{column, method, outlier_count, samples}]",
        "trends":              "list[{date_column, value_column, direction, r_squared, slope}]",
        "performance_summary": "{top_performers, bottom_performers}",
    },
    "charts": "list[chart_spec]  — KPICards + BarChart + LineChart",
    "errors": "dict[step → error_message]  — populated even on partial success",
}
