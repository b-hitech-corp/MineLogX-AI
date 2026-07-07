"""
insight_extractor — Tool 4
Detects anomalies, trends, and notable patterns in fleet data.

Uses statistical and rule-based methods so results are reproducible.
The LLM interprets the findings — it does not generate the numbers.
"""

from __future__ import annotations

from typing import Optional
import pandas as pd
import numpy as np

from data_analysis_agent.tools.csv_loader import get_dataframe


# ---------------------------------------------------------------------------
# Outlier detection
# ---------------------------------------------------------------------------


def detect_outliers(
    file_path: str,
    column: str,
    *,
    method: str = "iqr",  # "iqr" | "zscore"
    threshold: float = 1.5,  # IQR multiplier or Z-score cutoff
    entity_column: Optional[str] = None,
) -> dict:
    """
    Identify statistical outliers in a numeric column.

    Parameters
    ----------
    column        : str   Numeric column to inspect.
    method        : str   "iqr" (interquartile range) or "zscore".
    threshold     : float IQR multiplier (default 1.5) or Z-score cutoff (default 3).
    entity_column : str   If provided, include entity IDs in results.
    """
    df = get_dataframe(file_path).copy()

    if column not in df.columns:
        return {"error": f"Column '{column}' not found."}

    series = pd.to_numeric(df[column], errors="coerce").dropna()

    if method == "iqr":
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - threshold * iqr
        upper = q3 + threshold * iqr
        mask = (df[column] < lower) | (df[column] > upper)
    elif method == "zscore":
        z = (df[column] - df[column].mean()) / df[column].std()
        mask = z.abs() > threshold
        lower, upper = (
            float(df[column].mean() - threshold * df[column].std()),
            float(df[column].mean() + threshold * df[column].std()),
        )
    else:
        return {"error": "method must be 'iqr' or 'zscore'"}

    outlier_df = df[mask]
    cols = [column]
    if entity_column and entity_column in df.columns:
        cols = [entity_column, column]

    return {
        "outlier_count": int(mask.sum()),
        "total_rows": len(df),
        "outlier_pct": round(mask.mean() * 100, 1),
        "lower_bound": round(float(lower), 3),
        "upper_bound": round(float(upper), 3),
        "method": method,
        "threshold": threshold,
        "outlier_samples": outlier_df[cols].head(20).round(3).to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------


def detect_trend(
    file_path: str,
    date_column: str,
    value_column: str,
    *,
    freq: str = "W",
) -> dict:
    """
    Fit a linear trend to a time-aggregated series and report direction + strength.

    Returns slope (units/period), R², and a classification:
    "improving", "declining", "stable".
    """
    df = get_dataframe(file_path).copy()

    df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
    df = df.dropna(subset=[date_column, value_column])
    df = df.set_index(date_column)

    series = df[value_column].resample(freq).mean().dropna()
    if len(series) < 3:
        return {"error": "Not enough data points to compute trend (need ≥ 3 periods)."}

    x = np.arange(len(series), dtype=float)
    y = series.values.astype(float)

    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    pct_change = ((y[-1] - y[0]) / abs(y[0]) * 100) if y[0] != 0 else 0.0

    if abs(pct_change) < 5 or r_squared < 0.3:
        direction = "stable"
    elif slope > 0:
        direction = "increasing"
    else:
        direction = "decreasing"

    return {
        "value_column": value_column,
        "periods": len(series),
        "freq": freq,
        "slope_per_period": round(float(slope), 4),
        "r_squared": round(float(r_squared), 3),
        "pct_change_first_to_last": round(float(pct_change), 1),
        "direction": direction,
        "period_values": [
            {"date": str(d.date()), "value": round(float(v), 3)}
            for d, v in zip(series.index, series.values)
        ],
    }


# ---------------------------------------------------------------------------
# Threshold / SLA breach detection
# ---------------------------------------------------------------------------


def check_thresholds(
    file_path: str,
    rules: list[dict],
) -> dict:
    """
    Check a list of threshold rules and return breaching rows.

    Each rule is a dict: {"column": str, "operator": ">"|"<"|">="|"<="|"==",
                          "value": float, "label": str}

    Example:
        rules=[
            {"column": "idle_rate_pct", "operator": ">", "value": 30,
             "label": "High idle rate"},
            {"column": "fuel_efficiency_km_l", "operator": "<", "value": 5,
             "label": "Low fuel efficiency"},
        ]
    """
    df = get_dataframe(file_path)
    ops = {">": "__gt__", "<": "__lt__", ">=": "__ge__", "<=": "__le__", "==": "__eq__"}

    findings = []
    for rule in rules:
        col, op, val, label = (
            rule.get("column"),
            rule.get("operator"),
            rule.get("value"),
            rule.get("label", rule.get("column")),
        )
        if col not in df.columns:
            findings.append({"label": label, "error": f"Column '{col}' not found."})
            continue
        if op not in ops:
            findings.append({"label": label, "error": f"Unknown operator '{op}'."})
            continue

        method = ops[op]
        mask = getattr(pd.to_numeric(df[col], errors="coerce"), method)(val)
        count = int(mask.sum())
        findings.append(
            {
                "label": label,
                "rule": f"{col} {op} {val}",
                "breach_count": count,
                "breach_pct": round(mask.mean() * 100, 1),
                "sample_values": sorted(
                    df.loc[mask, col].dropna().astype(float).round(3).tolist()
                )[:10],
            }
        )

    return {"threshold_findings": findings, "rules_checked": len(rules)}


# ---------------------------------------------------------------------------
# Top / bottom performers
# ---------------------------------------------------------------------------


def fleet_performance_summary(
    file_path: str,
    metric_column: str,
    entity_column: str,
    *,
    top_n: int = 5,
) -> dict:
    """Return top and bottom N performers for quick executive summary."""
    df = get_dataframe(file_path)

    for col in (metric_column, entity_column):
        if col not in df.columns:
            return {"error": f"Column '{col}' not found."}

    grouped = df.groupby(entity_column)[metric_column].mean().round(3)
    top = grouped.nlargest(top_n)
    bottom = grouped.nsmallest(top_n)

    return {
        "metric": metric_column,
        "entity": entity_column,
        "top_performers": [
            {entity_column: str(e), metric_column: float(v)} for e, v in top.items()
        ],
        "bottom_performers": [
            {entity_column: str(e), metric_column: float(v)} for e, v in bottom.items()
        ],
        "fleet_average": round(float(grouped.mean()), 3),
        "fleet_std": round(float(grouped.std()), 3),
    }
