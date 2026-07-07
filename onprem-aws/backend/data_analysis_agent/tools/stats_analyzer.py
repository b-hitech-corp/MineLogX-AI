"""
stats_analyzer — Tool 3
Computes descriptive statistics, rankings, distributions, and
time-series aggregations over fleet data.

All numeric outputs come from pandas/scipy — the LLM interprets, not calculates.
"""

from __future__ import annotations

from typing import Optional
import pandas as pd

try:
    from scipy import stats as scipy_stats  # noqa: F401

    _SCIPY = True
except ImportError:
    _SCIPY = False

from data_analysis_agent.tools.csv_loader import get_dataframe


def describe_columns(
    file_path: str,
    columns: Optional[list[str]] = None,
) -> dict:
    """
    Full descriptive statistics for numeric columns.

    Returns count, mean, std, min, 25th/50th/75th percentile, max,
    skewness, and kurtosis for each selected column.
    """
    df = get_dataframe(file_path)
    numeric = df.select_dtypes(include="number")

    if columns:
        missing = [c for c in columns if c not in numeric.columns]
        if missing:
            return {"error": f"Columns not found or not numeric: {missing}"}
        numeric = numeric[columns]

    desc = numeric.describe(percentiles=[0.25, 0.5, 0.75]).T.round(3)
    result = desc.to_dict(orient="index")

    # Add skewness and kurtosis
    for col in numeric.columns:
        result[col]["skewness"] = round(float(numeric[col].skew()), 3)
        result[col]["kurtosis"] = round(float(numeric[col].kurtosis()), 3)

    return {"statistics": result, "columns_analyzed": list(numeric.columns)}


def rank_entities(
    file_path: str,
    metric_column: str,
    entity_column: str,
    *,
    top_n: int = 10,
    ascending: bool = False,
    agg_func: str = "mean",
) -> dict:
    """
    Rank fleet entities (vehicles, drivers, routes) by a metric.

    Parameters
    ----------
    metric_column : str   Column to rank by (e.g. "fuel_litres")
    entity_column : str   Entity to group on (e.g. "vehicle_id")
    top_n         : int   How many results to return
    ascending     : bool  True = bottom performers first
    agg_func      : str   "mean" | "sum" | "max" | "min" | "count"
    """
    df = get_dataframe(file_path)

    for col in (metric_column, entity_column):
        if col not in df.columns:
            return {"error": f"Column '{col}' not found. Available: {list(df.columns)}"}

    agg_map = {
        "mean": "mean",
        "sum": "sum",
        "max": "max",
        "min": "min",
        "count": "count",
    }
    if agg_func not in agg_map:
        return {"error": f"agg_func must be one of {list(agg_map.keys())}"}

    ranked = (
        df.groupby(entity_column)[metric_column]
        .agg(agg_func)
        .sort_values(ascending=ascending)
        .head(top_n)
        .round(3)
    )

    return {
        "ranking": [
            {"rank": i + 1, entity_column: str(entity), metric_column: float(val)}
            for i, (entity, val) in enumerate(ranked.items())
        ],
        "metric": metric_column,
        "entity": entity_column,
        "aggregation": agg_func,
        "direction": "ascending" if ascending else "descending",
    }


def time_series_aggregation(
    file_path: str,
    date_column: str,
    value_columns: list[str],
    *,
    freq: str = "W",  # "D"=daily, "W"=weekly, "ME"=month-end
    agg_func: str = "sum",
    group_by: Optional[str] = None,
) -> dict:
    """
    Aggregate one or more numeric columns over time, optionally per group.

    Returns a time-indexed list suitable for chart rendering.
    """
    df = get_dataframe(file_path)

    if date_column not in df.columns:
        return {"error": f"Date column '{date_column}' not found."}

    df = df.copy()
    df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
    df = df.dropna(subset=[date_column])

    missing_vals = [c for c in value_columns if c not in df.columns]
    if missing_vals:
        return {"error": f"Value columns not found: {missing_vals}"}

    df = df.set_index(date_column)

    if group_by and group_by in df.columns:
        groups = {}
        for gval, gdf in df.groupby(group_by):
            resampled = gdf[value_columns].resample(freq).agg(agg_func).round(3)
            groups[str(gval)] = _df_to_records(resampled)
        return {"series": groups, "freq": freq, "agg_func": agg_func}

    resampled = df[value_columns].resample(freq).agg(agg_func).round(3)
    return {
        "series": _df_to_records(resampled),
        "freq": freq,
        "agg_func": agg_func,
        "date_column": date_column,
        "value_columns": value_columns,
    }


def correlation_matrix(
    file_path: str,
    columns: Optional[list[str]] = None,
) -> dict:
    """Pearson correlation matrix for numeric columns."""
    df = get_dataframe(file_path)
    numeric = df.select_dtypes(include="number")
    if columns:
        numeric = numeric[[c for c in columns if c in numeric.columns]]

    corr = numeric.corr(method="pearson").round(3)
    return {
        "correlation_matrix": corr.to_dict(),
        "columns": list(corr.columns),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    records = []
    for ts, row in df.iterrows():
        entry = {"date": str(ts.date()) if hasattr(ts, "date") else str(ts)}
        entry.update({col: (None if pd.isna(val) else val) for col, val in row.items()})
        records.append(entry)
    return records
