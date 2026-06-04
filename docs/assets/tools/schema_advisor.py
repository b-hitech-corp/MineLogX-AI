"""
schema_advisor — Tool 6
Analyses a loaded CSV schema and returns a grounded analytics capability map:
which columns are entities, timestamps, and metrics; which KPIs are computable
with the available columns; and what analyses are recommended.

Call this immediately after csv_loader.load_csv() for any new file.
The output grounds all subsequent tool calls so the agent never references
columns that do not exist in the data.
"""
from __future__ import annotations

import ast
import pandas as pd

from config.kpi_formulas import KPI_REGISTRY
from tools.csv_loader import get_dataframe, load_csv


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def discover_schema(file_path: str) -> dict:
    """
    Analyse the schema of a previously loaded CSV and return an analytics
    capability map with entity columns, datetime columns, metric columns,
    feasible KPIs, and recommended analyses.

    Parameters
    ----------
    file_path : str
        Same key used when calling csv_loader.load_csv().

    Returns
    -------
    dict with:
        entity_columns     — good candidates for group_by / entity ranking
        datetime_columns   — temporal columns for time series and trends
        metric_columns     — numeric measurements for KPIs, stats, outliers
        categorical_columns — label/category columns for filtering
        feasible_kpis      — KPI names computable with available columns
        infeasible_kpis    — KPIs with their missing required columns
        timestamp_pairs    — (start, end) column pairs implying durations
        recommended_analyses — concrete next-step suggestions
        summary            — one-paragraph human-readable capability description
    """
    schema = load_csv(file_path)   # cheap: always a cache hit after load_csv was called
    columns: list[dict] = schema.get("columns", [])
    col_names = [c["name"] for c in columns]

    entity_cols      = _classify(columns, "entity")
    datetime_cols    = _classify(columns, "datetime")
    metric_cols      = _classify(columns, "metric")
    categorical_cols = _classify(columns, "categorical")

    feasible_kpis, infeasible_kpis = _assess_kpi_feasibility(col_names)
    ts_pairs   = _find_timestamp_pairs(columns)
    recommended = _build_recommendations(
        entity_cols, datetime_cols, metric_cols,
        feasible_kpis, ts_pairs, schema,
    )

    return {
        "file_path":            file_path,
        "row_count":            schema.get("row_count"),
        "entity_columns":       entity_cols,
        "datetime_columns":     datetime_cols,
        "metric_columns":       metric_cols,
        "categorical_columns":  categorical_cols,
        "feasible_kpis":        feasible_kpis,
        "infeasible_kpis":      infeasible_kpis,
        "timestamp_pairs":      ts_pairs,
        "recommended_analyses": recommended,
        "summary":              _build_summary(
            schema, entity_cols, datetime_cols, metric_cols, feasible_kpis
        ),
    }


# ---------------------------------------------------------------------------
# Column classification
# ---------------------------------------------------------------------------

def _classify(columns: list[dict], kind: str) -> list[str]:
    if kind == "entity":
        return [
            c["name"] for c in columns
            if c["name"].endswith("_id") or c["name"].endswith("_code")
            or c["name"].endswith("_num") or c["name"].endswith("_no")
        ]
    if kind == "datetime":
        return [c["name"] for c in columns if c["type"] == "datetime"]
    if kind == "metric":
        return [
            c["name"] for c in columns
            if c["type"] in ("float", "integer")
            and not any(c["name"].endswith(s) for s in ("_id", "_code", "_num", "_no"))
        ]
    if kind == "categorical":
        return [
            c["name"] for c in columns
            if c["type"] in ("categorical", "string")
            and not any(c["name"].endswith(s) for s in ("_id", "_code", "_num", "_no"))
        ]
    return []


# ---------------------------------------------------------------------------
# KPI feasibility — tries each KPI on a synthetic row; catches missing-column errors
# ---------------------------------------------------------------------------

def _assess_kpi_feasibility(col_names: list[str]) -> tuple[list[str], list[dict]]:
    """
    Build a 1-row synthetic DataFrame with all available columns set to 1.0
    and try each KPI. Values of 1.0 avoid division-by-zero so the only
    expected failure mode is a missing-column ValueError from _require_cols.
    """
    test_df = pd.DataFrame({col: [1.0] for col in col_names})
    feasible: list[str] = []
    infeasible: list[dict] = []

    for name, kpi in KPI_REGISTRY.items():
        try:
            kpi.compute(test_df)
            feasible.append(name)
        except ValueError as exc:
            msg = str(exc)
            if "Required columns missing" in msg:
                missing_cols = _parse_missing_cols(msg)
                infeasible.append({"kpi": name, "missing_columns": missing_cols})
            else:
                # ValueError from data logic, not missing columns — treat as feasible
                feasible.append(name)
        except Exception:
            # Column check passed; arithmetic error on synthetic values — still feasible
            feasible.append(name)

    return feasible, infeasible


def _parse_missing_cols(error_msg: str) -> list[str]:
    """Extract the list from 'Required columns missing from dataset: [...]'."""
    try:
        bracket_part = error_msg.split(": ", 1)[1]
        return ast.literal_eval(bracket_part)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Timestamp pairs — detect start/end column pairs implying event durations
# ---------------------------------------------------------------------------

_START_WORDS = ("load", "start", "begin", "open", "enter", "arriv", "depart_from")
_END_WORDS   = ("dump", "end", "finish", "close", "exit", "arriv_at", "depart")


def _find_timestamp_pairs(columns: list[dict]) -> list[dict]:
    ts_cols = [c["name"] for c in columns if c["type"] == "datetime"]
    pairs = []
    for s in ts_cols:
        for e in ts_cols:
            if s != e and (
                any(w in s for w in _START_WORDS) and any(w in e for w in _END_WORDS)
            ):
                pairs.append({"start": s, "end": e})
    return pairs


# ---------------------------------------------------------------------------
# Recommendations and summary
# ---------------------------------------------------------------------------

def _build_recommendations(
    entity_cols: list[str],
    datetime_cols: list[str],
    metric_cols: list[str],
    feasible_kpis: list[str],
    ts_pairs: list[dict],
    schema: dict,
) -> list[str]:
    recs: list[str] = []

    if feasible_kpis:
        shown = feasible_kpis[:4]
        suffix = " and more" if len(feasible_kpis) > 4 else ""
        recs.append(f"Calculate KPIs: {', '.join(shown)}{suffix}.")

    if metric_cols and entity_cols:
        recs.append(
            f"Rank {entity_cols[0]} by {metric_cols[0]} "
            f"to identify top and bottom performers."
        )

    if datetime_cols and metric_cols:
        recs.append(
            f"Aggregate {metric_cols[0]} over time using {datetime_cols[0]} "
            f"to detect trends (use freq='W' for weekly or 'ME' for monthly)."
        )

    if metric_cols:
        grp = f" grouped by {entity_cols[0]}" if entity_cols else ""
        recs.append(f"Detect outliers in {metric_cols[0]}{grp}.")

    for pair in ts_pairs:
        recs.append(
            f"Analyse event duration between {pair['start']} and {pair['end']} "
            f"for cycle time and throughput insights."
        )

    return recs


def _build_summary(
    schema: dict,
    entity_cols: list[str],
    datetime_cols: list[str],
    metric_cols: list[str],
    feasible_kpis: list[str],
) -> str:
    parts = [
        f"'{schema.get('file_path')}' — "
        f"{schema.get('row_count')} rows, {schema.get('column_count')} columns."
    ]
    if entity_cols:
        parts.append(f"Entity/ID columns: {', '.join(entity_cols)}.")
    if datetime_cols:
        parts.append(f"Temporal columns: {', '.join(datetime_cols)}.")
    if metric_cols:
        parts.append(f"Metric columns: {', '.join(metric_cols)}.")
    if feasible_kpis:
        parts.append(f"Computable KPIs: {', '.join(feasible_kpis)}.")
    else:
        parts.append(
            "No standard KPIs are directly computable with these columns — "
            "use stats_analyzer and insight_extractor directly on the metric columns above."
        )
    return " ".join(parts)
