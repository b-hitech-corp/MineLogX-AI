"""
kpi_engine — Tool 2
Executes KPI formulas against a loaded DataFrame.

The LLM asks for KPIs by name; this tool computes them deterministically
from the config/kpi_formulas registry. No numeric reasoning happens in
the model — all arithmetic is done here in Python/pandas.
"""

from __future__ import annotations

from typing import Optional

from data_analysis_agent.config.kpi_formulas import KPI_REGISTRY, list_kpis
from data_analysis_agent.tools.csv_loader import get_dataframe


def calculate_kpi(
    file_path: str,
    kpi_names: list[str],
    *,
    group_by: Optional[str] = None,
    filter_expr: Optional[str] = None,
    column_mapping: Optional[dict] = None,
    direct_kpi_mapping: Optional[dict] = None,
) -> dict:
    """
    Calculate one or more KPIs from a loaded CSV file.

    Parameters
    ----------
    file_path : str
        Key used when load_csv() was called.
    kpi_names : list[str]
        KPI identifiers from the registry (e.g. ["fuel_efficiency", "idle_rate"]).
        Pass ["*"] to compute all applicable KPIs.
    group_by : str, optional
        Column name to compute KPIs per group (e.g. "vehicle_id" or "region").
    filter_expr : str, optional
        A pandas query string applied before computing (e.g. "region == 'North'").

    Returns
    -------
    dict with keys: kpis (results), metadata (formulas used, filters applied)
    """
    df = get_dataframe(file_path)

    # Apply column mapping: rename actual columns to the variable names the
    # KPI formulas expect (e.g. "fuel_volume_l" → "fuel_litres").
    if column_mapping:
        rename = {
            actual: var
            for var, actual in column_mapping.items()
            if actual and actual in df.columns and actual != var
        }
        if rename:
            df = df.rename(columns=rename)

    if filter_expr:
        try:
            df = df.query(filter_expr)
        except Exception as exc:
            return {"error": f"filter_expr failed: {exc}"}

    if not df.shape[0]:
        return {
            "error": "DataFrame is empty after applying filter.",
            "filter": filter_expr,
        }

    # Resolve KPI names
    if kpi_names == ["*"]:
        kpi_names = list(KPI_REGISTRY.keys())

    results: dict = {}
    errors: dict = {}
    metadata: list[dict] = []

    for name in kpi_names:
        kpi = KPI_REGISTRY.get(name)
        if kpi is None:
            errors[name] = (
                f"Unknown KPI '{name}'. Available: {list(KPI_REGISTRY.keys())}"
            )
            continue

        # Direct column: the CSV already contains the pre-computed KPI value.
        # Skip the formula and read the column directly.
        if direct_kpi_mapping and name in direct_kpi_mapping:
            col = direct_kpi_mapping[name]
            if col in df.columns:
                val = round(float(df[col].mean()), 3)
                results[name] = {
                    "value": val,
                    "unit": kpi.unit,
                    "source": "direct_column",
                }
                metadata.append(
                    {
                        "kpi": name,
                        "description": kpi.description,
                        "formula": f"direct read from column '{col}' (mean)",
                        "unit": kpi.unit,
                    }
                )
                continue

        if group_by and group_by in df.columns:
            # Per-group computation
            group_results = {}
            for group_val, group_df in df.groupby(group_by):
                try:
                    val = kpi.compute(group_df)
                    group_results[str(group_val)] = val
                except Exception as exc:
                    group_results[str(group_val)] = {"error": str(exc)}
            results[name] = {
                "by_group": group_results,
                "group_column": group_by,
                "unit": kpi.unit,
            }
        else:
            try:
                val = kpi.compute(df)
                results[name] = {"value": val, "unit": kpi.unit}
            except Exception as exc:
                errors[name] = str(exc)

        metadata.append(
            {
                "kpi": name,
                "description": kpi.description,
                "formula": kpi.formula_doc,
                "unit": kpi.unit,
            }
        )

    return {
        "kpis": results,
        "errors": errors if errors else None,
        "metadata": metadata,
        "row_count_used": len(df),
        "filter_applied": filter_expr,
        "grouped_by": group_by,
    }


def available_kpis() -> dict:
    """Return the full KPI catalogue (called by the LLM to discover options)."""
    return {"available_kpis": list_kpis()}
