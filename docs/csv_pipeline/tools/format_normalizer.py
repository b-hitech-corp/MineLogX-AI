"""
format_normalizer — Stage 2 of the CSV Vectorization Pipeline.

Reads the schema_descriptor.json produced by Stage 1, executes the LLM-generated
transformation recipe against the raw CSV, and writes a clean canonical parquet
file to S3.

The normalizer is a recipe executor: it applies an ordered list of named operations
to the DataFrame. Adding support for a new CSV format requires only registering a
new op_* function — no changes to Stage 1 or the LLM prompt.

Streaming vs full-load
----------------------
Operations that require the full DataFrame in memory (transpose, pivot_segments,
melt, combine_header_rows, set_header_row) trigger a full-load read. All other
operations are applied chunk-by-chunk, keeping memory usage bounded for large files.

Public API
----------
    normalize(file_path, schema_descriptor, local_mode) -> NormalizeResult
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from csv_pipeline.config.settings import settings

logger = logging.getLogger(__name__)

STREAM_CHUNK_SIZE = 10_000   # rows per chunk when streaming

# Operations that cannot be applied incrementally — require the full DataFrame
_FULL_LOAD_OPS: set[str] = {
    "set_header_row",
    "combine_header_rows",
    "transpose",
    "pivot_segments",
    "melt",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class NormalizeResult:
    output_s3_key:  Optional[str]
    row_count:      int
    column_count:   int
    applied_steps:  list[str]         = field(default_factory=list)
    skipped_steps:  list[str]         = field(default_factory=list)
    errors:         list[str]         = field(default_factory=list)


# ---------------------------------------------------------------------------
# Operation implementations
# Each function receives (df: pd.DataFrame, params: dict) and returns pd.DataFrame.
# ---------------------------------------------------------------------------

def op_skip_rows(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Drop the first n rows (post-read metadata rows). Resets the index."""
    n = int(params.get("n", 0))
    return df.iloc[n:].reset_index(drop=True)


def op_set_header_row(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Promote row at position `row` to column names. All rows up to and
    including that position are dropped; index is reset.
    """
    row = int(params.get("row", 0))
    if row >= len(df):
        raise ValueError(f"set_header_row: row {row} out of range ({len(df)} rows)")
    new_cols = df.iloc[row].astype(str).str.strip().tolist()
    return df.iloc[row + 1:].reset_index(drop=True).rename(
        columns=dict(zip(df.columns, new_cols))
    )


def op_combine_header_rows(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Merge multiple header rows (by position) into a single header by joining
    their non-null, non-empty values with '_'. All header rows are dropped.
    """
    rows: list[int] = [int(r) for r in params.get("rows", [0])]
    if not rows:
        return df
    header_parts = [df.iloc[r].astype(str).str.strip() for r in rows]
    combined = header_parts[0]
    for part in header_parts[1:]:
        combined = combined + "_" + part
    combined = combined.str.replace(r"_+", "_", regex=True).str.strip("_")
    new_df = df.iloc[max(rows) + 1:].reset_index(drop=True)
    new_df.columns = combined.tolist()
    return new_df


def op_transpose(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Transpose the DataFrame. Promotes the first row of the transposed result
    to column names unless `promote_header=False` is set.
    """
    transposed = df.T.reset_index(drop=True)
    if params.get("promote_header", True):
        new_cols = transposed.iloc[0].astype(str).str.strip().tolist()
        transposed = transposed.iloc[1:].reset_index(drop=True)
        transposed.columns = new_cols
    return transposed


def op_pivot_segments(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Pivot a long-format segmented table to wide format.
    params: segment_col (the column whose distinct values become new columns),
            value_col (the column whose values fill the new columns),
            index_cols (optional, auto-detected if omitted).
    """
    segment_col = params.get("segment_col")
    value_col   = params.get("value_col")
    if not segment_col or not value_col:
        raise ValueError("pivot_segments requires 'segment_col' and 'value_col' params")
    for required in (segment_col, value_col):
        if required not in df.columns:
            raise ValueError(
                f"pivot_segments: column '{required}' not found in DataFrame. "
                f"Available columns: {list(df.columns)}"
            )

    index_cols = params.get("index_cols")
    if index_cols is None:
        index_cols = [c for c in df.columns if c not in (segment_col, value_col)]

    return (
        df.pivot_table(
            index=index_cols,
            columns=segment_col,
            values=value_col,
            aggfunc="first",
        )
        .reset_index()
        .rename_axis(columns=None)
    )


def op_melt(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Unpivot (melt) wide-format columns to long format.
    params: id_vars, value_vars, var_name (default "variable"), value_name (default "value").
    """
    return df.melt(
        id_vars    = params.get("id_vars"),
        value_vars = params.get("value_vars"),
        var_name   = params.get("var_name", "variable"),
        value_name = params.get("value_name", "value"),
    ).reset_index(drop=True)


def op_filter_rows(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Remove rows matching a pandas query expression.
    params: exclude_expr — e.g. "truck_id == 'TOTAL'" removes rows where that holds.
    Uses df.query() which supports the full pandas query syntax including `in` expressions.
    """
    expr = params.get("exclude_expr", "")
    if not expr:
        return df
    try:
        # query() returns rows that MATCH; we want to EXCLUDE those rows.
        matching_idx = df.query(expr).index
        return df.drop(index=matching_idx).reset_index(drop=True)
    except Exception as exc:
        logger.warning("filter_rows: query('%s') failed (%s); skipping", expr, exc)
        return df


def op_rename_columns(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Rename columns.
    params: mapping — dict of {old_name: new_name}.
    """
    mapping = params.get("mapping", {})
    return df.rename(columns=mapping)


def op_fill_forward(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Forward-fill a sparse column (e.g. segment headers repeated only on first row).
    params: column — the column name to ffill.
    """
    col = params.get("column")
    if col and col in df.columns:
        df = df.copy()
        df[col] = df[col].ffill()
    return df


def op_drop_columns(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Drop columns by name. Silently ignores names that don't exist.
    params: names — list of column names.
    """
    names = [n for n in params.get("names", []) if n in df.columns]
    return df.drop(columns=names)


# ---------------------------------------------------------------------------
# Operation registry — add new operations here without touching any other code
# ---------------------------------------------------------------------------

OPERATION_REGISTRY: dict[str, Callable[[pd.DataFrame, dict], pd.DataFrame]] = {
    "skip_rows":           op_skip_rows,
    "set_header_row":      op_set_header_row,
    "combine_header_rows": op_combine_header_rows,
    "transpose":           op_transpose,
    "pivot_segments":      op_pivot_segments,
    "melt":                op_melt,
    "filter_rows":         op_filter_rows,
    "rename_columns":      op_rename_columns,
    "fill_forward":        op_fill_forward,
    "drop_columns":        op_drop_columns,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize(
    file_path: str,
    schema_descriptor: dict,
    local_mode: bool = False,
) -> NormalizeResult:
    """
    Execute the transformation recipe from schema_descriptor and write a
    canonical parquet file to S3.

    Parameters
    ----------
    file_path         : S3 key or local path relative to sample_data/.
    schema_descriptor : dict output of schema_advisor.inspect_schema_sampled().
                        Must not be None; pass {} for a pass-through run.
    local_mode        : Read from sample_data/ instead of S3.

    Returns
    -------
    NormalizeResult with S3 key, row/column counts, and any errors.
    """
    if not isinstance(schema_descriptor, dict):
        return NormalizeResult(
            output_s3_key=None, row_count=0, column_count=0,
            errors=[f"schema_descriptor must be a dict, got {type(schema_descriptor).__name__}"],
        )

    steps: list[dict] = schema_descriptor.get("transformation_steps", [])
    s3_key            = _s3_output_key(file_path)

    applied:  list[str] = []
    skipped:  list[str] = []
    errors:   list[str] = []

    # Decide execution mode
    needs_full_load = any(s.get("operation") in _FULL_LOAD_OPS for s in steps)

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = tmp.name

    parse_dates  = _datetime_cols_from_descriptor(schema_descriptor)
    canonical_map = (schema_descriptor.get("canonical") or {}).get("canonical_resolution", {})
    row_count   = col_count = 0
    try:
        # ── Normalization (may fail; zeros are correct if it does) ────────
        if needs_full_load:
            row_count, col_count = _full_load_normalize(
                file_path, steps, tmp_path, local_mode, applied, skipped, errors,
                parse_dates=parse_dates, canonical_map=canonical_map,
            )
        else:
            row_count, col_count = _stream_normalize(
                file_path, steps, tmp_path, local_mode, applied, skipped, errors,
                parse_dates=parse_dates, canonical_map=canonical_map,
            )
    except Exception as exc:
        errors.append(f"normalization failed: {exc}")
        logger.exception("[format_normalizer] Normalization failed for '%s': %s", file_path, exc)
        s3_key = None
    else:
        # ── Upload / local save (row_count stays valid even on upload failure) ──
        try:
            if not local_mode:
                _upload_to_s3(tmp_path, s3_key)
                logger.info(
                    "[format_normalizer] '%s' → s3://%s/%s (%d rows, %d cols)",
                    file_path, settings.s3.bucket_name, s3_key, row_count, col_count,
                )
            else:
                import shutil
                local_out = Path(settings.local_data_path) / _local_output_path(file_path)
                local_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(tmp_path, local_out)
                logger.info("[format_normalizer] '%s' → %s", file_path, local_out)
        except Exception as exc:
            errors.append(f"upload failed: {exc}")
            logger.exception("[format_normalizer] Upload failed for '%s': %s", file_path, exc)
            s3_key = None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return NormalizeResult(
        output_s3_key = s3_key if not local_mode else None,
        row_count     = row_count,
        column_count  = col_count,
        applied_steps = applied,
        skipped_steps = skipped,
        errors        = errors,
    )


# ---------------------------------------------------------------------------
# Execution modes
# ---------------------------------------------------------------------------

def _datetime_cols_from_descriptor(schema_descriptor: dict) -> list[str]:
    """Extract column names inferred as datetime from the schema descriptor."""
    return [
        col for col, stats in schema_descriptor.get("column_stats", {}).items()
        if isinstance(stats, dict) and stats.get("inferred_type") == "datetime"
    ]


def _coerce_datetimes(df: pd.DataFrame, date_cols: list[str] | None) -> None:
    """Convert the named columns to datetime in place, after header cleaning.

    Only columns actually present are coerced (a missing column is data, not an
    error). Unparseable values become NaT rather than aborting the file.
    """
    for col in (date_cols or []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")


def _apply_canonical_rename(df: pd.DataFrame, canonical_map: dict | None) -> None:
    """Rename matched source columns to their canonical names in place.

    `canonical_map` is descriptor["canonical"]["canonical_resolution"]
    (cleaned source name -> canonical field). Columns not in the map (unknown /
    unmapped) keep their raw names so no data is lost - they are flagged
    elsewhere. The mapping is bijective on the matched subset, so no collisions.
    """
    if not canonical_map:
        return
    rename = {src: canon for src, canon in canonical_map.items()
              if src in df.columns and src != canon}
    if rename:
        df.rename(columns=rename, inplace=True)


def _stream_normalize(
    file_path: str,
    steps: list[dict],
    output_path: str,
    local_mode: bool,
    applied: list[str],
    skipped: list[str],
    errors:  list[str],
    parse_dates: list[str] | None = None,
    canonical_map: dict | None = None,
) -> tuple[int, int]:
    """
    Apply streamable transformation steps chunk-by-chunk.
    Writes an incrementally-built parquet file to output_path.
    Returns (row_count, column_count).
    """
    fh = _open_stream(file_path, local_mode)
    writer:     Optional[pq.ParquetWriter] = None
    row_count   = 0
    col_count   = 0
    first_chunk = True

    try:
        for chunk in pd.read_csv(
            fh,
            chunksize=STREAM_CHUNK_SIZE,
            low_memory=False,
        ):
            chunk.columns = [c.strip().lower().replace(" ", "_") for c in chunk.columns]
            # Coerce datetimes AFTER cleaning headers. The descriptor stores
            # cleaned column names, so parsing at read time (raw headers) would
            # break on any non-lowercase/spaced header. Coerce only columns that
            # actually exist; bad values become NaT instead of aborting the file.
            _coerce_datetimes(chunk, parse_dates)

            for step in steps:
                op   = step.get("operation", "")
                params = step.get("params", {})
                fn   = OPERATION_REGISTRY.get(op)
                if fn is None:
                    if first_chunk:
                        skipped.append(op)
                        logger.warning("[format_normalizer] Unknown operation '%s', skipping", op)
                    continue
                try:
                    chunk = fn(chunk, params)
                    if first_chunk:  # record each step only once, not once per chunk
                        applied.append(op)
                except Exception as exc:
                    errors.append(f"{op}: {exc}")
                    logger.warning("[format_normalizer] op '%s' failed on chunk: %s", op, exc)

            # Canonicalize column names so the parquet speaks canonical vocabulary.
            _apply_canonical_rename(chunk, canonical_map)

            table = pa.Table.from_pandas(chunk, preserve_index=False)

            if writer is None:
                writer = pq.ParquetWriter(output_path, table.schema)
            else:
                # Cast to first-chunk schema to avoid type drift between chunks.
                # On failure, log and write the table as-is; ParquetWriter will
                # raise on schema mismatch, which is caught by the outer handler.
                try:
                    table = table.cast(writer.schema_arrow)
                except Exception as cast_exc:
                    logger.warning(
                        "[format_normalizer] Schema cast failed for chunk of '%s': %s",
                        file_path, cast_exc,
                    )
                    errors.append(f"schema_cast: {cast_exc}")

            writer.write_table(table)
            row_count += len(chunk)
            col_count  = len(chunk.columns)
            first_chunk = False

    finally:
        if writer:
            writer.close()
        if hasattr(fh, "close"):
            fh.close()

    return row_count, col_count


def _full_load_normalize(
    file_path: str,
    steps: list[dict],
    output_path: str,
    local_mode: bool,
    applied: list[str],
    skipped: list[str],
    errors:  list[str],
    parse_dates: list[str] | None = None,
    canonical_map: dict | None = None,
) -> tuple[int, int]:
    """
    Load the entire CSV into memory, apply all transformation steps, write parquet.
    Used when the recipe includes full-load-only operations (transpose, pivot, etc.).
    Returns (row_count, column_count).
    """
    fh = _open_stream(file_path, local_mode)
    try:
        df = pd.read_csv(fh, low_memory=False)
    finally:
        if hasattr(fh, "close"):
            fh.close()

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    # Coerce datetimes after cleaning headers (see _stream_normalize note).
    _coerce_datetimes(df, parse_dates)

    for step in steps:
        op     = step.get("operation", "")
        params = step.get("params", {})
        fn     = OPERATION_REGISTRY.get(op)
        if fn is None:
            skipped.append(op)
            logger.warning("[format_normalizer] Unknown operation '%s', skipping", op)
            continue
        try:
            df = fn(df, params)
            applied.append(op)
        except Exception as exc:
            errors.append(f"{op}: {exc}")
            logger.warning("[format_normalizer] op '%s' failed: %s", op, exc)

    # Canonicalize column names so the parquet speaks canonical vocabulary.
    _apply_canonical_rename(df, canonical_map)

    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, output_path)
    return len(df), len(df.columns)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_stream(file_path: str, local_mode: bool):
    """Return a file-like object for pd.read_csv()."""
    if local_mode:
        return open(Path(settings.local_data_path) / file_path, "rb")
    s3  = boto3.client("s3", region_name=settings.s3.region)
    obj = s3.get_object(
        Bucket=settings.s3.bucket_name,
        Key=settings.s3.prefix + file_path,
    )
    return io.BytesIO(obj["Body"].read())


def _upload_to_s3(local_path: str, s3_key: str) -> None:
    s3 = boto3.client("s3", region_name=settings.s3.region)
    s3.upload_file(local_path, settings.s3.bucket_name, s3_key)


def _s3_output_key(file_path: str) -> str:
    p      = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    return f"{settings.s3.prefix}vectorization/{folder}/canonical/{p.stem}.parquet"


def _local_output_path(file_path: str) -> str:
    p      = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    return f"vectorization/{folder}/canonical/{p.stem}.parquet"
