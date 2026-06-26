"""
csv_sampler — Stage 1 streaming profiler for the CSV Vectorization Pipeline.

Streams through CSV files of any size without loading them fully into memory,
computing per-column statistics and flagging structural anomalies. Builds the
compact, information-dense input sent to Claude for schema inspection.

Public API
----------
    stream_and_profile(file_path, local_mode) -> StreamProfile
    build_llm_input(profile)                  -> str
"""
from __future__ import annotations

import io
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import boto3
import pandas as pd

from csv_pipeline.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FULL_FILE_ROW_LIMIT  = 8_000   # files ≤ this are sent to Claude in full
STREAM_CHUNK_SIZE    = 2_000   # rows per pandas streaming chunk
HEAD_ROWS            = 50      # rows collected from the start of the file
TAIL_ROWS            = 20      # rows collected from the end (rolling buffer)
MAX_ANOMALY_ROWS     = 100     # max anomalous rows passed to Claude
MAX_SAMPLE_VALUES    = 10      # unique sample values stored per column
CARDINALITY_CAP      = 1_000   # stop counting unique values above this

_DATE_NAME_PATTERNS  = ("date", "time", "timestamp", "_at", "_on", "created", "updated")
_HEADER_PATTERN      = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{1,60}$")


# ---------------------------------------------------------------------------
# Output data classes
# ---------------------------------------------------------------------------

@dataclass
class ColumnStats:
    name:               str
    inferred_type:      str            # float | integer | datetime | categorical | string
    null_count:         int   = 0
    total_count:        int   = 0
    min_val:            Optional[float] = None
    max_val:            Optional[float] = None
    mean:               Optional[float] = None
    sample_values:      list[str] = field(default_factory=list)
    cardinality:        int  = 0
    cardinality_capped: bool = False

    @property
    def null_pct(self) -> float:
        return round(self.null_count / max(self.total_count, 1) * 100, 1)


@dataclass
class AnomalyRecord:
    row_index:    int
    anomaly_type: str   # embedded_header | separator_row | type_break | column_count_shift
    detail:       str
    row_data:     dict


@dataclass
class StreamProfile:
    row_count:       int
    column_count:    int
    column_names:    list[str]
    column_stats:    dict[str, ColumnStats]
    anomaly_records: list[AnomalyRecord]
    send_full_file:  bool
    head_rows:       list[dict]
    tail_rows:       list[dict]
    anomaly_rows:    list[dict]          # row_data of each AnomalyRecord, deduped
    full_df:         Optional[pd.DataFrame] = None  # only set when send_full_file=True


# ---------------------------------------------------------------------------
# Internal accumulator (not exposed)
# ---------------------------------------------------------------------------

class _ColAccumulator:
    """Accumulates per-column statistics using the Welford online algorithm."""

    def __init__(self, name: str) -> None:
        self.name          = name
        self.null_count    = 0
        self.total_count   = 0
        # Welford
        self._n     = 0
        self._mean  = 0.0
        self._M2    = 0.0
        self._min: Optional[float] = None
        self._max: Optional[float] = None
        # Cardinality / samples
        self._seen:            set[str] = set()
        self._cardinality_cap  = False
        self._sample_values:   list[str] = []
        # Type voting
        self._numeric_ticks  = 0
        self._datetime_ticks = 0
        self._string_ticks   = 0

    # ------------------------------------------------------------------

    def update(self, value: Any, col_is_datetime: bool) -> None:
        self.total_count += 1
        if pd.isna(value):
            self.null_count += 1
            return

        sv = str(value)[:80]

        # Cardinality
        if not self._cardinality_cap:
            self._seen.add(sv)
            if len(self._seen) >= CARDINALITY_CAP:
                self._cardinality_cap = True

        # Sample values
        if len(self._sample_values) < MAX_SAMPLE_VALUES and sv not in self._sample_values:
            self._sample_values.append(sv)

        # Type tick
        if col_is_datetime:
            self._datetime_ticks += 1
        elif isinstance(value, (int, float)):
            self._numeric_ticks += 1
            self._update_welford(float(value))
        else:
            self._string_ticks += 1

    def _update_welford(self, v: float) -> None:
        self._n += 1
        delta = v - self._mean
        self._mean += delta / self._n
        self._M2 += delta * (v - self._mean)
        if self._min is None or v < self._min:
            self._min = v
        if self._max is None or v > self._max:
            self._max = v

    # ------------------------------------------------------------------

    def to_stats(self, inferred_type: str) -> ColumnStats:
        non_null = self.total_count - self.null_count
        return ColumnStats(
            name               = self.name,
            inferred_type      = inferred_type,
            null_count         = self.null_count,
            total_count        = self.total_count,
            min_val            = round(self._min, 4) if self._min is not None else None,
            max_val            = round(self._max, 4) if self._max is not None else None,
            mean               = round(self._mean, 4) if self._n > 0 else None,
            sample_values      = list(self._sample_values),
            cardinality        = len(self._seen),
            cardinality_capped = self._cardinality_cap,
        )

    def infer_type(self, col_name: str) -> str:
        """Infer the column's semantic type from accumulated data."""
        if self._datetime_ticks > 0:
            return "datetime"
        if any(p in col_name.lower() for p in _DATE_NAME_PATTERNS):
            return "datetime"
        non_null = self.total_count - self.null_count
        if non_null == 0:
            return "string"
        if self._numeric_ticks / max(non_null, 1) >= 0.9:
            # Distinguish integer vs float from sample values
            if all("." not in sv for sv in self._sample_values if sv):
                return "integer"
            return "float"
        if len(self._seen) / max(non_null, 1) < 0.05:
            return "categorical"
        return "string"


# ---------------------------------------------------------------------------
# Anomaly detection helpers
# ---------------------------------------------------------------------------

def _is_header_like_row(row: pd.Series) -> bool:
    """True if ≥50% of values match column-name patterns."""
    non_null = [v for v in row if pd.notna(v)]
    if not non_null:
        return False
    matches = sum(1 for v in non_null if isinstance(v, str) and _HEADER_PATTERN.match(v.strip()))
    return matches / len(non_null) >= 0.5


def _is_separator_row(row: pd.Series) -> bool:
    """True if ≥80% of values are null or empty."""
    total = len(row)
    empty = sum(1 for v in row if pd.isna(v) or str(v).strip() == "")
    return empty / max(total, 1) >= 0.8


def _detect_anomalies_in_chunk(
    chunk: pd.DataFrame,
    chunk_start_idx: int,
    expected_col_count: int,
    numeric_col_names: set[str],
    first_chunk: bool,
) -> list[AnomalyRecord]:
    """Return anomaly records for any suspicious rows in this chunk."""
    records: list[AnomalyRecord] = []

    # Column count shift (whole-chunk anomaly, only after first chunk)
    if not first_chunk and len(chunk.columns) != expected_col_count:
        records.append(AnomalyRecord(
            row_index    = chunk_start_idx,
            anomaly_type = "column_count_shift",
            detail       = f"Expected {expected_col_count} columns, got {len(chunk.columns)}",
            row_data     = chunk.iloc[0].to_dict(),
        ))
        return records  # rest of detection unreliable for this chunk

    for local_pos, (_, row) in enumerate(chunk.iterrows()):
        global_idx = chunk_start_idx + local_pos
        if first_chunk and global_idx < 1:
            continue  # skip first data row to avoid false-positive header detection

        if _is_separator_row(row):
            records.append(AnomalyRecord(
                row_index    = global_idx,
                anomaly_type = "separator_row",
                detail       = "≥80% of values are null or empty",
                row_data     = row.to_dict(),
            ))
            continue

        if _is_header_like_row(row):
            records.append(AnomalyRecord(
                row_index    = global_idx,
                anomaly_type = "embedded_header",
                detail       = "≥50% of values match column-name patterns",
                row_data     = row.to_dict(),
            ))
            continue

        # Type break — numeric column contains a non-numeric string
        for col in numeric_col_names:
            if col not in chunk.columns:
                continue
            val = row.get(col)
            if isinstance(val, str) and val.strip() and val.strip().lower() not in {
                "nan", "null", "none", "na", "n/a", "",
            }:
                records.append(AnomalyRecord(
                    row_index    = global_idx,
                    anomaly_type = "type_break",
                    detail       = f"Numeric column '{col}' contains string value '{val[:40]}'",
                    row_data     = row.to_dict(),
                ))
                break  # one anomaly per row is enough

    return records


# ---------------------------------------------------------------------------
# S3 / local fetch helpers
# ---------------------------------------------------------------------------

def _open_csv_stream(file_path: str, local_mode: bool):
    """Return a file-like object suitable for pd.read_csv(chunksize=...)."""
    if local_mode:
        full_path = Path(settings.local_data_path) / file_path
        return open(full_path, "rb")   # caller is responsible for closing
    s3 = boto3.client("s3", region_name=settings.s3.region)
    obj = s3.get_object(
        Bucket=settings.s3.bucket_name,
        Key=settings.s3.prefix + file_path,
    )
    # Read into BytesIO so pandas can seek (StreamingBody is not seekable)
    raw = obj["Body"].read()
    return io.BytesIO(raw)


# ---------------------------------------------------------------------------
# Public: stream_and_profile
# ---------------------------------------------------------------------------

def stream_and_profile(
    file_path: str,
    local_mode: bool = False,
) -> StreamProfile:
    """
    Stream through the entire CSV file and return a StreamProfile with:
      - per-column statistics (computed from all rows via Welford algorithm)
      - structural anomaly records (embedded headers, separator rows, type breaks)
      - sampled rows for the LLM (head, tail, anomaly rows)
      - a flag indicating whether the full DataFrame should be sent to Claude

    Never loads the entire file into memory for large files.
    For files ≤ FULL_FILE_ROW_LIMIT rows, the full DataFrame is retained.
    """
    logger.info("[csv_sampler] Profiling '%s' (local=%s)", file_path, local_mode)

    fh = _open_csv_stream(file_path, local_mode)
    try:
        chunks = pd.read_csv(fh, chunksize=STREAM_CHUNK_SIZE, low_memory=False)

        accumulators:       dict[str, _ColAccumulator] = {}
        anomaly_records:    list[AnomalyRecord]        = []
        head_rows:          list[dict]                 = []
        tail_buffer:        deque[dict]                = deque(maxlen=TAIL_ROWS)
        all_rows_for_full:  list[dict]                 = []  # only if small file

        row_count           = 0
        column_names:       list[str] = []
        expected_col_count  = 0
        numeric_col_names:  set[str]  = set()
        first_chunk         = True

        for chunk in chunks:
            # Normalise column names (same convention as csv_loader)
            chunk.columns = [c.strip().lower().replace(" ", "_") for c in chunk.columns]
            chunk_start   = row_count

            if first_chunk:
                column_names        = list(chunk.columns)
                expected_col_count  = len(column_names)
                for col in column_names:
                    accumulators[col] = _ColAccumulator(col)

            # Determine which columns pandas inferred as numeric this chunk
            for col in chunk.columns:
                if col in accumulators and pd.api.types.is_numeric_dtype(chunk[col]):
                    numeric_col_names.add(col)

            # Detect datetime columns by name
            datetime_col_set = {
                col for col in chunk.columns
                if any(p in col.lower() for p in _DATE_NAME_PATTERNS)
            }

            # Accumulate statistics row-by-row for each column
            for col in chunk.columns:
                if col not in accumulators:
                    continue   # column_count_shift case
                is_dt = col in datetime_col_set
                for val in chunk[col]:
                    accumulators[col].update(val, is_dt)

            # Anomaly detection
            if len(anomaly_records) < MAX_ANOMALY_ROWS:
                new_anomalies = _detect_anomalies_in_chunk(
                    chunk, chunk_start, expected_col_count,
                    numeric_col_names, first_chunk,
                )
                anomaly_records.extend(new_anomalies)

            # Collect head rows (first HEAD_ROWS rows)
            if row_count < HEAD_ROWS:
                available = HEAD_ROWS - row_count
                for _, row in chunk.head(available).iterrows():
                    head_rows.append(row.to_dict())

            # Rolling tail buffer
            for _, row in chunk.iterrows():
                tail_buffer.append(row.to_dict())

            # Full file accumulation (small files only)
            if row_count + len(chunk) <= FULL_FILE_ROW_LIMIT:
                all_rows_for_full.extend(chunk.to_dict(orient="records"))

            row_count  += len(chunk)
            first_chunk = False

    finally:
        if hasattr(fh, "close"):
            fh.close()

    # ------------------------------------------------------------------
    # Edge case: empty file (header-only) — loop never executed
    # ------------------------------------------------------------------
    if not column_names:
        fh2 = _open_csv_stream(file_path, local_mode)
        try:
            header_df  = pd.read_csv(fh2, nrows=0)
            header_df.columns = [c.strip().lower().replace(" ", "_") for c in header_df.columns]
            column_names = list(header_df.columns)
        except Exception:
            pass
        finally:
            if hasattr(fh2, "close"):
                fh2.close()

    # ------------------------------------------------------------------
    # Infer final column types and build ColumnStats
    # ------------------------------------------------------------------
    column_stats: dict[str, ColumnStats] = {}
    for col, acc in accumulators.items():
        inferred_type      = acc.infer_type(col)
        column_stats[col]  = acc.to_stats(inferred_type)

    # ------------------------------------------------------------------
    # Determine whether to send the full file to Claude
    # ------------------------------------------------------------------
    send_full_file = row_count <= FULL_FILE_ROW_LIMIT
    full_df        = None
    if send_full_file and all_rows_for_full:
        full_df = pd.DataFrame(all_rows_for_full)

    # Deduplicate anomaly rows (keep first occurrence per row_index)
    seen_indices: set[int] = set()
    deduped_anomaly_rows: list[dict] = []
    for record in anomaly_records:
        if record.row_index not in seen_indices:
            seen_indices.add(record.row_index)
            deduped_anomaly_rows.append(record.row_data)

    logger.info(
        "[csv_sampler] '%s': %d rows, %d cols, %d anomalies, send_full=%s",
        file_path, row_count, len(column_names),
        len(anomaly_records), send_full_file,
    )

    return StreamProfile(
        row_count       = row_count,
        column_count    = len(column_names),
        column_names    = column_names,
        column_stats    = column_stats,
        anomaly_records = anomaly_records,
        send_full_file  = send_full_file,
        head_rows       = head_rows,
        tail_rows       = list(tail_buffer),
        anomaly_rows    = deduped_anomaly_rows,
        full_df         = full_df,
    )


# ---------------------------------------------------------------------------
# Public: build_llm_input
# ---------------------------------------------------------------------------

def build_llm_input(profile: StreamProfile) -> str:
    """
    Format a StreamProfile into the compact text representation sent to Claude.
    Statistics are on one line per column; sample rows are minimal CSV blocks.
    """
    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────
    lines.append(f"Total rows: {profile.row_count:,}  |  Columns: {profile.column_count}")
    lines.append("")

    # ── Column statistics ────────────────────────────────────────────────
    lines.append("COLUMN STATISTICS:")
    for col, stats in profile.column_stats.items():
        parts = [f"  {col:<40} [{stats.inferred_type}]"]
        if stats.inferred_type in ("float", "integer"):
            parts.append(
                f"mean={stats.mean}  min={stats.min_val}  max={stats.max_val}"
                f"  null={stats.null_pct}%"
            )
        elif stats.inferred_type == "datetime":
            sample = stats.sample_values[:2] if stats.sample_values else []
            parts.append(f"sample={sample}  null={stats.null_pct}%")
        else:
            cap  = "+" if stats.cardinality_capped else ""
            samp = stats.sample_values[:5]
            parts.append(
                f"cardinality={stats.cardinality}{cap}"
                f"  sample={samp}  null={stats.null_pct}%"
            )
        lines.append("  ".join(parts))
    lines.append("")

    # ── Structural sample ────────────────────────────────────────────────
    lines.append("STRUCTURAL SAMPLE:")

    if profile.send_full_file and profile.full_df is not None:
        lines.append(f"--- FULL FILE ({profile.row_count} rows) ---")
        lines.append(profile.full_df.to_csv(index=False))
    else:
        # Head
        if profile.head_rows:
            lines.append(f"--- HEAD (first {len(profile.head_rows)} rows) ---")
            lines.append(_rows_to_csv(profile.head_rows, profile.column_names))

        # Anomaly rows
        if profile.anomaly_rows:
            lines.append(f"--- ANOMALY ROWS ({len(profile.anomaly_records)} detected) ---")
            for record in profile.anomaly_records[:MAX_ANOMALY_ROWS]:
                lines.append(
                    f"  [Row {record.row_index} — {record.anomaly_type}: {record.detail}]"
                )
            lines.append(_rows_to_csv(profile.anomaly_rows, profile.column_names))

        # Tail
        if profile.tail_rows:
            lines.append(f"--- TAIL (last {len(profile.tail_rows)} rows) ---")
            lines.append(_rows_to_csv(profile.tail_rows, profile.column_names))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _rows_to_csv(rows: list[dict], column_names: list[str]) -> str:
    """Render a list of row dicts as a compact CSV string."""
    if not rows:
        return ""
    df = pd.DataFrame(rows, columns=column_names)
    return df.to_csv(index=False)
