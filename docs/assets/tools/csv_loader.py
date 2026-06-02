"""
csv_loader — Tool 1
Fetches CSV files from S3 (or local fallback), parses them with pandas,
infers the schema, and returns a structured description the LLM can reason about.

The parsed DataFrame is cached in memory so subsequent tool calls don't
re-fetch from S3.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Optional

import boto3
import pandas as pd

from config.settings import settings

# ---------------------------------------------------------------------------
# In-process DataFrame cache
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[pd.DataFrame, float]] = {}   # key → (df, timestamp)


def _cache_get(key: str) -> Optional[pd.DataFrame]:
    if key in _cache:
        df, ts = _cache[key]
        if time.time() - ts < settings.cache_ttl_seconds:
            return df
    return None


def _cache_set(key: str, df: pd.DataFrame) -> None:
    _cache[key] = (df, time.time())


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def load_csv(
    file_path: str,
    *,
    date_columns: Optional[list[str]] = None,
    use_local_fallback: bool = False,
) -> dict:
    """
    Load a CSV file from S3 and return schema + preview.

    Parameters
    ----------
    file_path : str
        S3 key (e.g. "fleet/vehicles_2024_05.csv") or local path when
        use_local_fallback=True.
    date_columns : list[str], optional
        Column names that should be parsed as datetime.
    use_local_fallback : bool
        When True, read from the local sample_data/ directory.
        Used during development and testing.

    Returns
    -------
    dict with keys:
        file_path, row_count, column_count, columns (schema),
        date_range (if date columns found), numeric_summary, preview_rows
    """
    cache_key = file_path

    df = _cache_get(cache_key)
    if df is None:
        df = _fetch(file_path, date_columns=date_columns, local=use_local_fallback)
        _cache_set(cache_key, df)

    return _describe(df, file_path)


def get_dataframe(file_path: str) -> pd.DataFrame:
    """Return the cached DataFrame for a previously loaded file."""
    df = _cache_get(file_path)
    if df is None:
        raise ValueError(
            f"File '{file_path}' has not been loaded yet. Call load_csv() first."
        )
    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch(
    file_path: str,
    *,
    date_columns: Optional[list[str]],
    local: bool,
) -> pd.DataFrame:
    if local:
        full_path = Path(settings.local_data_path) / file_path
        raw = full_path.read_bytes()
    else:
        s3 = boto3.client("s3", region_name=settings.s3.region)
        obj = s3.get_object(
            Bucket=settings.s3.bucket_name,
            Key=settings.s3.prefix + file_path,
        )
        raw = obj["Body"].read()

    parse_dates = date_columns or False
    df = pd.read_csv(io.BytesIO(raw), parse_dates=parse_dates, low_memory=False)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _infer_column_type(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    # Heuristic: if nulls < 5% and cardinality < 50, likely categorical
    non_null = series.dropna()
    if len(non_null) > 0 and non_null.nunique() / len(non_null) < 0.05:
        return "categorical"
    return "string"


def _describe(df: pd.DataFrame, file_path: str) -> dict:
    columns = []
    for col in df.columns:
        col_type = _infer_column_type(df[col])
        info: dict = {
            "name": col,
            "type": col_type,
            "null_count": int(df[col].isna().sum()),
            "null_pct": round(df[col].isna().mean() * 100, 1),
        }
        if col_type in ("integer", "float"):
            info.update({
                "min": float(df[col].min()) if pd.notna(df[col].min()) else None,
                "max": float(df[col].max()) if pd.notna(df[col].max()) else None,
                "mean": round(float(df[col].mean()), 3) if pd.notna(df[col].mean()) else None,
            })
        elif col_type == "categorical":
            top = df[col].value_counts().head(5).to_dict()
            info["top_values"] = {str(k): int(v) for k, v in top.items()}
        elif col_type == "datetime":
            info["min_date"] = str(df[col].min())
            info["max_date"] = str(df[col].max())
        columns.append(info)

    # Date range (from any datetime column)
    datetime_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    date_range = None
    if datetime_cols:
        col = datetime_cols[0]
        date_range = {"column": col, "start": str(df[col].min()), "end": str(df[col].max())}

    result = {
        "file_path": file_path,
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": columns,
        "date_range": date_range,
        "preview_rows": json.loads(df.head(3).to_json(orient="records", date_format="iso")),
    }
    return result
