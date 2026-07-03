"""
chunker_serializer — Stage 3 of the CSV Vectorization Pipeline.

Reads the canonical parquet produced by Stage 2, splits it into
semantically coherent windows, serializes each window to natural-language
text, and writes the resulting chunks as newline-delimited JSON (JSONL)
to S3 — ready for embedding and OpenSearch ingestion in Stage 4.

No LLM call in this stage. All serialization is deterministic Python.

Chunking strategies
-------------------
time_window  — groups rows into calendar windows (default 7 days) ordered
               by the primary datetime column. Consecutive windows overlap
               by `overlap_rows` rows to avoid cutting mid-event.
row_count    — fixed-size sliding windows used when no datetime column
               is available in the schema descriptor.

Public API
----------
    chunk_and_serialize(file_path, schema_descriptor, ...) -> ChunkResult
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

import boto3
import pandas as pd
import pyarrow.parquet as pq

from csv_pipeline.config.canonical_schema import CANONICAL_SCHEMA
from csv_pipeline.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name suffixes that indicate a cumulative total (use sum, not mean)
# ---------------------------------------------------------------------------
_SUM_SUFFIXES  = (
    "_total", "_count", "_tonnes", "_km", "_litres", "_l",
    "_kg", "_events", "_trips", "_cycles", "_loads",
)
_RATE_SUFFIXES = (
    "_rate", "_pct", "_ratio", "_efficiency", "_utilization",
    "_compliance", "_accuracy", "_intensity", "_score",
)

MAX_ENTITY_VALUES_SHOWN = 5   # unique entity values listed in the text
MAX_OUTLIER_EXAMPLES    = 3   # outlier instances mentioned per column
OUTLIER_IQR_THRESHOLD   = 1.5


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ChunkResult:
    output_s3_key:      Optional[str]
    chunk_count:        int   = 0
    total_rows_chunked: int   = 0
    strategy_used:      str   = ""
    errors:             list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def chunk_and_serialize(
    file_path: str,
    schema_descriptor: dict,
    strategy: str = "time_window",
    window_days: int = 7,
    max_rows_per_chunk: int = 500,
    overlap_rows: int = 50,
    local_mode: bool = False,
) -> ChunkResult:
    """
    Chunk the canonical parquet and serialise each chunk to NL text.

    Parameters
    ----------
    file_path         : Original CSV S3 key (e.g. "C1/fuel_management_events.csv").
                        Used to locate the canonical parquet and name output artifacts.
    schema_descriptor : dict from schema_advisor.inspect_schema_sampled().
    strategy          : "time_window" or "row_count".
    window_days       : Calendar days per time window (time_window only).
    max_rows_per_chunk: Hard cap on rows per chunk after windowing.
    overlap_rows      : Rows from the end of chunk N prepended to chunk N+1.
    local_mode        : Read parquet from sample_data/ and write JSONL locally.

    Returns
    -------
    ChunkResult with S3 key, chunk count, and any errors.
    """
    if not isinstance(schema_descriptor, dict):
        return ChunkResult(
            output_s3_key=None,
            errors=[f"schema_descriptor must be a dict, got {type(schema_descriptor).__name__}"],
        )

    errors: list[str] = []

    # ── Step 1: Read canonical parquet ───────────────────────────────────
    try:
        df = _read_parquet(file_path, local_mode)
    except Exception as exc:
        return ChunkResult(output_s3_key=None, errors=[f"parquet read failed: {exc}"])

    if df.empty:
        return ChunkResult(output_s3_key=None, errors=["canonical parquet is empty"])

    # ── Step 2: Resolve column roles from schema descriptor ──────────────
    col_roles = _column_roles(df.columns.tolist(), schema_descriptor)

    entity_cols   = [c for c, r in col_roles.items() if r == "entity"]
    metric_cols   = [c for c, r in col_roles.items() if r == "metric"]
    datetime_cols = [c for c, r in col_roles.items() if r == "datetime"]
    cat_cols      = [c for c, r in col_roles.items() if r == "categorical"]

    # File-level canonical markers (same for every chunk): which canonical
    # fields are absent (domain-scoped) and which raw columns are unmapped.
    markers = _build_canonical_markers(schema_descriptor)

    # Parse datetime columns that are still strings
    for col in datetime_cols:
        if col in df.columns and not pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # ── Step 3: Choose chunking strategy ─────────────────────────────────
    primary_date_col = datetime_cols[0] if datetime_cols else None

    if strategy == "time_window" and primary_date_col:
        chunks       = _time_window_chunks(df, primary_date_col, window_days,
                                           max_rows_per_chunk, overlap_rows)
        strategy_used = "time_window"
    else:
        chunks        = _row_count_chunks(df, max_rows_per_chunk, overlap_rows)
        strategy_used = "row_count"
        if strategy == "time_window" and not primary_date_col:
            logger.warning(
                "[chunker] No datetime column found in '%s'; falling back to row_count.",
                file_path,
            )

    if not chunks:
        return ChunkResult(
            output_s3_key=None, strategy_used=strategy_used,
            errors=["chunking produced zero chunks"],
        )

    # ── Step 4: Serialize each chunk ─────────────────────────────────────
    p      = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    stem   = p.stem

    serialized: list[dict] = []
    total_rows = 0
    row_cursor = 0  # tracks cumulative rows emitted (including overlap)

    for idx, chunk in enumerate(chunks):
        try:
            record = _serialize_chunk(
                chunk       = chunk,
                chunk_index = idx,
                row_start   = row_cursor,
                file_path   = file_path,
                folder      = folder,
                stem        = stem,
                entity_cols  = entity_cols,
                metric_cols  = metric_cols,
                datetime_cols = datetime_cols,
                cat_cols     = cat_cols,
                schema_version = schema_descriptor.get("schema_version", "1.0"),
                markers      = markers,
            )
            serialized.append(record)
            total_rows += len(chunk)
            row_cursor += len(chunk)
        except Exception as exc:
            errors.append(f"chunk {idx}: serialization failed: {exc}")
            logger.warning("[chunker] Chunk %d serialization failed for '%s': %s",
                           idx, file_path, exc)

    # ── Step 5: Write JSONL ───────────────────────────────────────────────
    s3_key = _s3_chunks_key(file_path)
    try:
        _write_jsonl(serialized, s3_key, file_path, local_mode)
        logger.info("[chunker] '%s': %d chunks → %s", file_path, len(serialized), s3_key)
    except Exception as exc:
        errors.append(f"JSONL write failed: {exc}")
        s3_key = None

    return ChunkResult(
        output_s3_key      = s3_key if not local_mode else None,
        chunk_count        = len(serialized),
        total_rows_chunked = total_rows,
        strategy_used      = strategy_used,
        errors             = errors,
    )


# ---------------------------------------------------------------------------
# Chunking strategies
# ---------------------------------------------------------------------------

def _time_window_chunks(
    df: pd.DataFrame,
    date_col: str,
    window_days: int,
    max_rows: int,
    overlap_rows: int,
) -> list[pd.DataFrame]:
    """
    Split df into calendar windows of `window_days` length ordered by `date_col`.
    Each window includes the last `overlap_rows` rows of the preceding window as
    a prefix, so events near boundaries appear in both neighbours.
    """
    df = df.sort_values(date_col, na_position="last").reset_index(drop=True)
    df_valid = df.dropna(subset=[date_col])

    if df_valid.empty:
        return []

    t_min = df_valid[date_col].min()
    t_max = df_valid[date_col].max()
    delta = pd.Timedelta(days=window_days)

    chunks: list[pd.DataFrame] = []
    prev_tail: Optional[pd.DataFrame] = None
    window_start = t_min

    while window_start <= t_max:
        window_end = window_start + delta
        mask  = (df[date_col] >= window_start) & (df[date_col] < window_end)
        chunk = df[mask].reset_index(drop=True)

        if len(chunk) == 0:
            window_start = window_end
            continue

        # Cap at max_rows (take first max_rows of the window)
        if len(chunk) > max_rows:
            chunk = chunk.iloc[:max_rows].reset_index(drop=True)

        # Prepend overlap from previous chunk
        if prev_tail is not None and len(prev_tail) > 0:
            chunk = pd.concat([prev_tail, chunk]).reset_index(drop=True)

        chunks.append(chunk)
        prev_tail = chunk.iloc[-overlap_rows:].reset_index(drop=True) \
                    if len(chunk) >= overlap_rows else chunk.copy()
        window_start = window_end

    return chunks


def _row_count_chunks(
    df: pd.DataFrame,
    chunk_size: int,
    overlap_rows: int,
) -> list[pd.DataFrame]:
    """
    Fixed-size sliding windows. Each window starts `step` rows after the previous,
    where step = chunk_size - overlap_rows. Produces the standard overlap pattern.
    """
    if chunk_size <= overlap_rows:
        overlap_rows = chunk_size // 4  # safety: avoid infinite loop

    step   = max(1, chunk_size - overlap_rows)
    chunks = []

    for start in range(0, len(df), step):
        chunk = df.iloc[start: start + chunk_size].reset_index(drop=True)
        if not chunk.empty:
            chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_chunk(
    chunk: pd.DataFrame,
    chunk_index: int,
    row_start: int,
    file_path: str,
    folder: str,
    stem: str,
    entity_cols: list[str],
    metric_cols: list[str],
    datetime_cols: list[str],
    cat_cols: list[str],
    schema_version: str,
    markers: Optional[dict] = None,
) -> dict:
    """
    Convert a DataFrame chunk into a {text, metadata} dict ready for embedding.
    """
    markers = markers or {}
    row_end = row_start + len(chunk)

    # Date range
    date_range: Optional[dict] = None
    if datetime_cols:
        col = datetime_cols[0]
        if col in chunk.columns:
            dmin = chunk[col].min()
            dmax = chunk[col].max()
            if pd.notna(dmin) and pd.notna(dmax):
                date_range = {
                    "start": pd.Timestamp(dmin).isoformat(),
                    "end":   pd.Timestamp(dmax).isoformat(),
                }

    # Entity snapshot (unique values per entity column)
    entity_values: dict[str, list] = {}
    for col in entity_cols:
        if col in chunk.columns:
            uniques = chunk[col].dropna().unique().tolist()
            entity_values[col] = [str(v) for v in uniques[:MAX_ENTITY_VALUES_SHOWN]]

    text = _build_text(
        chunk, chunk_index, file_path, folder,
        entity_cols, metric_cols, datetime_cols, cat_cols, date_range, markers,
    )

    return {
        "chunk_id": f"{folder}/{stem}__chunk_{chunk_index:05d}",
        "text": text,
        "metadata": {
            "source_file":    file_path,
            "folder":         folder,
            "chunk_index":    chunk_index,
            "row_range":      [row_start, row_end],
            "date_range":     date_range,
            "entity_values":  entity_values,
            "schema_version": schema_version,
            "column_list":    chunk.columns.tolist(),
            "row_count":      len(chunk),
            # Canonical markers (file-level): the agent can tell "value null"
            # from "field not tracked here", and which raw columns are unmapped.
            "available_fields":         sorted(c for c in chunk.columns.tolist()),
            "missing_canonical_fields": markers.get("missing_canonical_fields", []),
            "unmapped_columns":         markers.get("unmapped_columns", []),
            "active_domains":           markers.get("active_domains", []),
        },
    }


def _build_text(
    chunk: pd.DataFrame,
    chunk_index: int,
    file_path: str,
    folder: str,
    entity_cols: list[str],
    metric_cols: list[str],
    datetime_cols: list[str],
    cat_cols: list[str],
    date_range: Optional[dict],
    markers: Optional[dict] = None,
) -> str:
    """
    Build a natural-language text representation of the chunk.
    Structure: context header -> metric summaries -> category counts ->
    outlier notes -> canonical coverage markers.
    """
    markers = markers or {}
    lines: list[str] = []

    # ── Context header ────────────────────────────────────────────────────
    header_parts = [f"Mining telemetry from {file_path}"]

    if date_range:
        header_parts.append(
            f"time {date_range['start']} to {date_range['end']}"
        )

    entity_ctx = []
    for col in entity_cols:
        if col not in chunk.columns:
            continue
        uniques = chunk[col].dropna().unique()
        if len(uniques) == 1:
            entity_ctx.append(f"{col}={uniques[0]}")
        elif len(uniques) <= MAX_ENTITY_VALUES_SHOWN:
            entity_ctx.append(f"{col}=[{', '.join(str(v) for v in uniques)}]")
        else:
            entity_ctx.append(f"{col} ({len(uniques)} entities)")

    if entity_ctx:
        header_parts.append(", ".join(entity_ctx))

    header_parts.append(f"{len(chunk)} records")
    lines.append(". ".join(header_parts) + ".")
    lines.append("")

    # ── Metric summaries ─────────────────────────────────────────────────
    outlier_notes: list[str] = []

    if metric_cols:
        lines.append("Measurements:")
        for col in metric_cols:
            if col not in chunk.columns:
                continue
            # Cast to float so boolean/integer columns yield roundable
            # aggregates (numpy 2.x bool/int scalars lack __round__).
            series = pd.to_numeric(chunk[col], errors="coerce").dropna().astype("float64")
            if series.empty:
                continue

            agg     = _agg_label(col)
            mean_v  = _safe_round(series.mean())
            min_v   = _safe_round(series.min())
            max_v   = _safe_round(series.max())
            total_v = _safe_round(series.sum())

            if agg == "sum":
                line = f"  {col}: total={total_v}, mean={mean_v}, max={max_v}"
            else:
                line = f"  {col}: mean={mean_v}, min={min_v}, max={max_v}"

            # IQR outlier detection
            outliers = _iqr_outliers(series)
            if not outliers.empty:
                line += f"  [{len(outliers)} outlier(s)]"
                # Collect examples for the outlier notes section
                for row_pos, val in outliers.head(MAX_OUTLIER_EXAMPLES).items():
                    ts = ""
                    if datetime_cols and datetime_cols[0] in chunk.columns:
                        ts_val = chunk[datetime_cols[0]].iloc[row_pos] \
                                 if row_pos < len(chunk) else None
                        if ts_val is not None and pd.notna(ts_val):
                            ts = f" at {pd.Timestamp(ts_val).isoformat()}"
                    outlier_notes.append(f"  {col}={_safe_round(val)}{ts}")

            lines.append(line)

    # ── Categorical counts ────────────────────────────────────────────────
    cat_present = [c for c in cat_cols if c in chunk.columns]
    if cat_present:
        lines.append("")
        lines.append("Categories:")
        for col in cat_present:
            counts = chunk[col].value_counts().head(5)
            parts  = [f"{k}:{v}" for k, v in counts.items()]
            lines.append(f"  {col}: {', '.join(parts)}")

    # ── Outlier notes ─────────────────────────────────────────────────────
    if outlier_notes:
        lines.append("")
        lines.append("Outliers detected:")
        lines.extend(outlier_notes)

    # ── Canonical coverage markers ────────────────────────────────────────
    # So the embedding/agent can distinguish "value is null" from "this dataset
    # does not track this field", and knows which raw columns are unmapped.
    missing = markers.get("missing_canonical_fields", [])
    if missing:
        lines.append("")
        lines.append(
            "Fields not tracked in this dataset: " + ", ".join(missing) + "."
        )
    unmapped = markers.get("unmapped_columns", [])
    if unmapped:
        lines.append("")
        lines.append(
            "Unmapped source columns (present but not in the canonical schema): "
            + ", ".join(unmapped) + "."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _column_roles(columns: list[str], schema_descriptor: dict) -> dict[str, str]:
    """
    Build a col->role mapping, preferring the deterministic canonical
    reconciliation (descriptor["canonical"]["resolved_fields"]) since Stage 2
    renamed matched columns to their canonical names. Columns with no canonical
    resolution (unknown / unmapped) fall back to the LLM classifications and
    then to inferred_type from column_stats.

    Canonical roles are authoritative: a bool field tagged `categorical` will
    therefore be summarized as counts, never sent through the numeric path.
    """
    role_map: dict[str, str] = {}

    # 1) Canonical resolution (authoritative for matched columns)
    resolved = (schema_descriptor.get("canonical") or {}).get("resolved_fields", {})
    for col in columns:
        if col in resolved and isinstance(resolved[col], dict):
            role_map[col] = resolved[col].get("role", "metadata")

    # 2) LLM classifications (for columns canonical did not resolve)
    for entry in schema_descriptor.get("column_classifications", []):
        name = entry.get("name", "")
        role = entry.get("role", "unknown")
        if name in columns and name not in role_map:
            role_map[name] = role

    # Fill gaps with inferred_type from column_stats
    _type_to_role = {
        "float":       "metric",
        "integer":     "metric",
        "datetime":    "datetime",
        "categorical": "categorical",
        "string":      "metadata",
    }
    for col in columns:
        if col in role_map:
            continue
        stats = schema_descriptor.get("column_stats", {}).get(col, {})
        inferred = stats.get("inferred_type", "string") if isinstance(stats, dict) else "string"
        # Apply ID-column heuristic for anything not yet classified
        if any(col.endswith(s) for s in ("_id", "_code", "_num", "_no")):
            role_map[col] = "entity"
        else:
            role_map[col] = _type_to_role.get(inferred, "metadata")

    return role_map


def _safe_round(value, ndigits: int = 3):
    """Round numerically but never crash on a non-numeric/odd scalar.

    Defense in depth: numpy 2.x bool_/odd scalars lack __round__, so a column
    mis-routed into the numeric path (or a surprising dtype) degrades gracefully
    instead of aborting the whole chunk. Correctness no longer depends solely on
    perfect role classification.
    """
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return value


def _build_canonical_markers(schema_descriptor: dict) -> dict:
    """File-level absence / unmapped markers derived from the canonical block.

    Absent canonical fields are scoped to the file's ACTIVE domains (+ shared)
    so the marker stays relevant - a tire file reports absent tire/shared fields,
    not the entire ~90-field universe. Unmapped columns are raw headers that
    matched no canonical field (flagged; kept in the data under their raw names).
    """
    canon = schema_descriptor.get("canonical") or {}
    active = set(canon.get("active_domains", [])) or {"shared"}
    scoped_absent = [
        f for f in canon.get("absent_fields", [])
        if CANONICAL_SCHEMA.get(f, {}).get("domain", "shared") in active
    ]
    return {
        "missing_canonical_fields": sorted(scoped_absent),
        "unmapped_columns":         list(canon.get("unknown_columns", [])),
        "active_domains":           sorted(active),
    }


def _agg_label(col_name: str) -> str:
    """Return 'sum' for cumulative columns, 'mean' for rates/averages."""
    return "sum" if any(col_name.endswith(s) for s in _SUM_SUFFIXES) else "mean"


def _iqr_outliers(series: pd.Series) -> pd.Series:
    """Return subset of series that are outliers by 1.5×IQR rule."""
    if len(series) < 4:
        return pd.Series(dtype=float)
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr    = q3 - q1
    if iqr == 0:
        return pd.Series(dtype=float)
    bound_upper = q3 + OUTLIER_IQR_THRESHOLD * iqr
    bound_lower = q1 - OUTLIER_IQR_THRESHOLD * iqr
    return series[(series > bound_upper) | (series < bound_lower)]


def _read_parquet(file_path: str, local_mode: bool) -> pd.DataFrame:
    """Read the canonical parquet produced by Stage 2."""
    p      = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    parquet_key = f"{settings.s3.prefix}vectorization/{folder}/canonical/{p.stem}.parquet"

    if local_mode:
        local_path = Path(settings.local_data_path) / \
                     f"vectorization/{folder}/canonical/{p.stem}.parquet"
        table = pq.read_table(str(local_path))
    else:
        s3  = boto3.client("s3", region_name=settings.s3.region)
        obj = s3.get_object(Bucket=settings.s3.bucket_name, Key=parquet_key)
        table = pq.read_table(io.BytesIO(obj["Body"].read()))

    return table.to_pandas()


def _s3_chunks_key(file_path: str) -> str:
    p      = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    return f"{settings.s3.prefix}vectorization/{folder}/chunks/{p.stem}.chunks.jsonl"


def _write_jsonl(
    records: list[dict],
    s3_key: str,
    file_path: str,
    local_mode: bool,
) -> None:
    """Write records as newline-delimited JSON to S3 or local disk."""
    payload = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in records)
    encoded = payload.encode("utf-8")

    if local_mode:
        p      = PurePosixPath(file_path)
        folder = str(p.parent)
        if folder in (".", ""):
            folder = "root"
        local_path = Path(settings.local_data_path) / \
                     f"vectorization/{folder}/chunks/{p.stem}.chunks.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(encoded)
        logger.info("[chunker] Written locally → %s", local_path)
        return

    s3 = boto3.client("s3", region_name=settings.s3.region)
    s3.put_object(
        Bucket      = settings.s3.bucket_name,
        Key         = s3_key,
        Body        = encoded,
        ContentType = "application/x-ndjson",
    )
