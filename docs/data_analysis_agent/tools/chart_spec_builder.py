"""
chart_spec_builder — Tool 5
Generates UI-framework-compatible JSON chart specifications from tool results.

Targets Recharts (React) by default; the schema is designed to be easily
adapted to Vega-Lite, Chart.js, or ApexCharts.

The LLM passes in data + intent; this tool returns a complete JSON spec
the frontend can render without further interpretation.
"""
from __future__ import annotations

from typing import Any, Optional
import colorsys


# ---------------------------------------------------------------------------
# Colour palette helpers
# ---------------------------------------------------------------------------

_BRAND_COLORS = [
    "#2563EB",  # blue
    "#16A34A",  # green
    "#DC2626",  # red
    "#D97706",  # amber
    "#7C3AED",  # violet
    "#0891B2",  # cyan
    "#DB2777",  # pink
    "#65A30D",  # lime
]


def _colors(n: int) -> list[str]:
    """Return n evenly distributed colours from the brand palette."""
    if n <= len(_BRAND_COLORS):
        return _BRAND_COLORS[:n]
    # Extend by interpolating HSL
    extras = []
    for i in range(n - len(_BRAND_COLORS)):
        h = (i / (n - len(_BRAND_COLORS))) * 360
        r, g, b = colorsys.hls_to_rgb(h / 360, 0.45, 0.65)
        extras.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return _BRAND_COLORS + extras


# ---------------------------------------------------------------------------
# Public builder functions
# ---------------------------------------------------------------------------

def build_line_chart(
    *,
    title: str,
    data: list[dict],           # [{date: "...", series1: v, series2: v}, ...]
    x_key: str,
    y_keys: list[str],
    y_label: Optional[str] = None,
    x_label: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Time-series or sequential line chart."""
    colors = _colors(len(y_keys))
    return {
        "chart_type": "LineChart",
        "library": "recharts",
        "title": title,
        "description": description,
        "data": data,
        "x_axis": {"key": x_key, "label": x_label or x_key},
        "y_axis": {"label": y_label or ""},
        "series": [
            {"key": k, "name": k.replace("_", " ").title(), "color": colors[i], "dot": False}
            for i, k in enumerate(y_keys)
        ],
        "tooltip": True,
        "legend": len(y_keys) > 1,
        "grid": True,
    }


def build_bar_chart(
    *,
    title: str,
    data: list[dict],           # [{category: "...", value: n}, ...]
    x_key: str,
    y_keys: list[str],
    y_label: Optional[str] = None,
    x_label: Optional[str] = None,
    layout: str = "vertical",   # "vertical" | "horizontal"
    stacked: bool = False,
    description: Optional[str] = None,
) -> dict:
    """Bar chart for rankings or category comparisons."""
    colors = _colors(len(y_keys))
    return {
        "chart_type": "BarChart",
        "library": "recharts",
        "title": title,
        "description": description,
        "data": data,
        "layout": layout,
        "x_axis": {"key": x_key, "label": x_label or x_key},
        "y_axis": {"label": y_label or ""},
        "series": [
            {"key": k, "name": k.replace("_", " ").title(), "color": colors[i], "stacked": stacked}
            for i, k in enumerate(y_keys)
        ],
        "tooltip": True,
        "legend": len(y_keys) > 1,
        "grid": True,
    }


def build_scatter_chart(
    *,
    title: str,
    data: list[dict],
    x_key: str,
    y_key: str,
    name_key: Optional[str] = None,
    color_key: Optional[str] = None,
    x_label: Optional[str] = None,
    y_label: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Scatter plot for correlation and outlier visualisation."""
    return {
        "chart_type": "ScatterChart",
        "library": "recharts",
        "title": title,
        "description": description,
        "data": data,
        "x_axis": {"key": x_key, "label": x_label or x_key},
        "y_axis": {"key": y_key, "label": y_label or y_key},
        "name_key": name_key,
        "color_key": color_key,
        "color": _BRAND_COLORS[0],
        "tooltip": True,
        "grid": True,
    }


def build_pie_chart(
    *,
    title: str,
    data: list[dict],           # [{name: "...", value: n}, ...]
    name_key: str = "name",
    value_key: str = "value",
    donut: bool = True,
    description: Optional[str] = None,
) -> dict:
    """Pie / donut chart for composition views."""
    colors = _colors(len(data))
    data_with_colors = [
        {**row, "_color": colors[i]} for i, row in enumerate(data)
    ]
    return {
        "chart_type": "PieChart",
        "library": "recharts",
        "title": title,
        "description": description,
        "data": data_with_colors,
        "name_key": name_key,
        "value_key": value_key,
        "donut": donut,
        "inner_radius": 60 if donut else 0,
        "outer_radius": 100,
        "tooltip": True,
        "legend": True,
    }


def build_kpi_cards(
    *,
    title: str,
    kpis: list[dict],   # [{"label": str, "value": any, "unit": str, "trend": str|None}]
    description: Optional[str] = None,
) -> dict:
    """Structured KPI summary card layout (not a Recharts chart but same spec pattern)."""
    return {
        "chart_type": "KPICards",
        "library": "custom",
        "title": title,
        "description": description,
        "cards": kpis,
    }


def build_heatmap(
    *,
    title: str,
    data: list[dict],           # [{row_label: str, col_label: str, value: float}]
    row_key: str,
    col_key: str,
    value_key: str,
    color_scheme: str = "blue",
    description: Optional[str] = None,
) -> dict:
    """Heatmap for correlation matrices, utilization grids, etc."""
    return {
        "chart_type": "Heatmap",
        "library": "custom",
        "title": title,
        "description": description,
        "data": data,
        "row_key": row_key,
        "col_key": col_key,
        "value_key": value_key,
        "color_scheme": color_scheme,
    }


# ---------------------------------------------------------------------------
# Convenience: build the right chart from a stats_analyzer time_series result
# ---------------------------------------------------------------------------

def chart_from_time_series(
    time_series_result: dict,
    *,
    title: str,
    description: Optional[str] = None,
) -> dict:
    """
    Wrap a stats_analyzer.time_series_aggregation() result in a line chart spec.
    Handles both flat and grouped series.
    """
    series_data = time_series_result.get("series", [])
    value_cols = time_series_result.get("value_columns", [])

    if isinstance(series_data, list):
        # Flat series — list of {date, col1, col2, ...}
        return build_line_chart(
            title=title,
            data=series_data,
            x_key="date",
            y_keys=value_cols,
            description=description,
        )

    # Grouped series — dict of {group_val: [{date, ...}, ...]}
    # Flatten: one series per group × value column
    flat_data: dict[str, dict] = {}
    series_keys = []
    for group, records in series_data.items():
        for rec in records:
            date = rec["date"]
            if date not in flat_data:
                flat_data[date] = {"date": date}
            for col in value_cols:
                key = f"{group}_{col}"
                if key not in series_keys:
                    series_keys.append(key)
                flat_data[date][key] = rec.get(col)

    return build_line_chart(
        title=title,
        data=sorted(flat_data.values(), key=lambda r: r["date"]),
        x_key="date",
        y_keys=series_keys,
        description=description,
    )
