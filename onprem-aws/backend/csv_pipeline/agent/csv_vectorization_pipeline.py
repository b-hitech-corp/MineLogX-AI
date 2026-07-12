"""
csv_vectorization_pipeline — Orchestrator for the CSV Vectorization Pipeline.

Runs Stages 1–4 in sequence for a single CSV file:
    1. Schema Inspection    csv_sampler + column_mapper → schema_descriptor.json (S3)
    2. Format Normalization format_normalizer            → canonical.ndjson       (S3)
    3. Chunk & Serialize    chunker_serializer           → chunks.jsonl           (S3)
    4. OpenSearch Ingest    opensearch_ingestor          → AOSS vector index

Idempotency
-----------
Each stage checks whether its output artifact already exists before running.
If the artifact is found, the stage is skipped and its output is loaded for
the next stage. Pass force=True to re-run all stages unconditionally.

Stages 1–3 use S3 artifact presence as the idempotency signal.
Stage 4 deletes existing documents for source_file before re-indexing so that
repeated runs never accumulate duplicates (AOSS NextGen vector collections do
not support custom document IDs, so delete-before-index is the clean pattern).
AOSS also does not support _delete_by_query at all (unconditional 404), so the
delete step searches for matching _ids and bulk-deletes them instead.

Per-stage error isolation
-------------------------
A stage failure does not block downstream stages when the required input
artifact is already available in S3.  For example, if Stage 2 fails but a
canonical NDJSON from a previous run is present, Stage 3 proceeds normally.

Stage selection
---------------
Pass stages=[1, 2, 3] to run only the first three stages (no AOSS needed —
useful for inspecting CSV artefacts before the cluster is ready).
Default runs all four stages.

Usage (SageMaker Notebook)
--------------------------
    # Run with the repo root (the parent of csv_pipeline/) as the working dir,
    # or add it to sys.path, then import the unit package fully-qualified:
    #     import sys; sys.path.insert(0, "/path/to/repo-root")

    from csv_pipeline.agent.csv_vectorization_pipeline import run_pipeline
    result = run_pipeline(
        file_path = "C1/fuel_management_events.csv",
        stages    = [1, 2, 3, 4],
    )
    print(result)

Public API
----------
    run_pipeline(file_path, ...)  -> PipelineResult
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from opensearchpy.helpers import bulk as os_bulk

from csv_pipeline.config.settings import settings
from csv_pipeline.tools.format_normalizer import normalize
from csv_pipeline.tools.chunker_serializer import chunk_and_serialize
from csv_pipeline.tools.opensearch_ingestor import ingest_chunks, _build_aoss_client
from csv_pipeline.tools.schema_inspector import inspect_schema_sampled

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    stage: int
    name: str
    skipped: bool = False
    success: bool = False
    artifact_key: Optional[str] = None  # S3 key of the output artifact
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def status_label(self) -> str:
        if self.skipped:
            return "SKIPPED"
        return "OK" if self.success else "FAILED"


@dataclass
class PipelineResult:
    file_path: str
    stage_results: list[StageResult] = field(default_factory=list)

    @property
    def overall_success(self) -> bool:
        return all(r.success or r.skipped for r in self.stage_results)

    @property
    def failed_stages(self) -> list[int]:
        return [r.stage for r in self.stage_results if not r.success and not r.skipped]

    def __str__(self) -> str:
        lines = [f"\nPipeline: '{self.file_path}'"]
        for r in self.stage_results:
            lines.append(
                f"  Stage {r.stage} ({r.name}): {r.status_label}  [{r.duration_s:.1f}s]"
            )
            if r.artifact_key:
                lines.append(f"    → {r.artifact_key}")
            for e in r.errors[:3]:
                lines.append(f"    ✗ {e}")
            if len(r.errors) > 3:
                lines.append(f"    … and {len(r.errors) - 3} more error(s)")
        overall = (
            "SUCCESS"
            if self.overall_success
            else f"FAILED (stages {self.failed_stages})"
        )
        lines.append(f"\nOverall: {overall}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pipeline(
    file_path: str,
    local_mode: bool = False,
    stages: Optional[list[int]] = None,
    force: bool = False,
    index_name: Optional[str] = None,
    backend: str = "bedrock",
    chunking_strategy: str = "time_window",
    window_days: int = 7,
    max_rows_per_chunk: int = 500,
    overlap_rows: int = 50,
) -> PipelineResult:
    """
    Run the CSV Vectorization Pipeline for a single file.

    Parameters
    ----------
    file_path         : S3 key (e.g. "C1/fuel_events.csv") or path relative
                        to sample_data/ when local_mode=True.
    local_mode        : Read CSV and write artefacts to sample_data/ on disk.
                        Stage 4 still connects to AOSS.
    stages            : Which stages to run, e.g. [1, 2, 3].
                        Default None runs all four.
    force             : Re-run every stage even if its artefact already exists.
    index_name        : Override settings.opensearch.index_name for Stage 4.
    backend           : LLM backend for Stage 1 — "bedrock" (default) or "ollama".
    chunking_strategy : "time_window" (default) or "row_count".
    window_days       : Calendar days per time window (time_window only).
    max_rows_per_chunk: Hard row cap per chunk.
    overlap_rows      : Rows shared between consecutive chunks.

    Returns
    -------
    PipelineResult — per-stage results and overall success flag.
    """
    active_stages = set(stages) if stages is not None else {1, 2, 3, 4}
    result = PipelineResult(file_path=file_path)
    artifacts = _artifact_paths(file_path)
    schema_descriptor: Optional[dict] = None

    logger.info(
        "[pipeline] Starting '%s'  stages=%s  force=%s",
        file_path,
        sorted(active_stages),
        force,
    )

    # ── Stage 1: Schema Inspection ────────────────────────────────────────
    if 1 in active_stages:
        s1 = _run_stage_1(
            file_path,
            local_mode,
            backend,
            force,
            artifacts["schema"],
        )
        result.stage_results.append(s1)
        if s1.success or s1.skipped:
            schema_descriptor = s1._descriptor  # passed via private attr (see below)
    else:
        # Stage 1 not requested — try to load an existing schema from S3
        schema_descriptor = _try_load_schema(artifacts["schema"], local_mode, file_path)

    # ── Stage 2: Format Normalization ─────────────────────────────────────
    if 2 in active_stages:
        s2 = _run_stage_2(
            file_path,
            local_mode,
            schema_descriptor,
            force,
            artifacts["canonical"],
        )
        result.stage_results.append(s2)

    # ── Stage 3: Chunk & Serialize ────────────────────────────────────────
    if 3 in active_stages:
        s3 = _run_stage_3(
            file_path,
            local_mode,
            schema_descriptor,
            force,
            artifacts["chunks"],
            chunking_strategy,
            window_days,
            max_rows_per_chunk,
            overlap_rows,
        )
        result.stage_results.append(s3)

    # ── Stage 4: OpenSearch Ingest ────────────────────────────────────────
    if 4 in active_stages:
        s4 = _run_stage_4(
            file_path,
            local_mode,
            index_name,
            force,
            artifacts["chunks"],
        )
        result.stage_results.append(s4)

    logger.info("[pipeline] Finished '%s': %s", file_path, result.overall_success)
    return result


# ---------------------------------------------------------------------------
# Per-stage runners
# ---------------------------------------------------------------------------


def _run_stage_1(
    file_path: str,
    local_mode: bool,
    backend: str,
    force: bool,
    schema_key: str,
) -> StageResult:
    r = StageResult(stage=1, name="Schema Inspection")
    r._descriptor = None  # type: ignore[attr-defined]

    # Idempotency check
    if not force and not local_mode and _s3_exists(schema_key):
        logger.info("[pipeline] Stage 1 SKIPPED — artefact exists: %s", schema_key)
        r.skipped = True
        r.success = True
        r.artifact_key = schema_key
        r._descriptor = _load_json_from_s3(schema_key)  # type: ignore[attr-defined]
        return r

    t0 = time.perf_counter()
    try:
        descriptor = inspect_schema_sampled(
            file_path, local_mode=local_mode, backend=backend
        )
        r._descriptor = descriptor  # type: ignore[attr-defined]
        r.artifact_key = descriptor.get("schema_s3_key") or schema_key
        r.success = True
        logger.info(
            "[pipeline] Stage 1 OK: %d rows, %d cols",
            descriptor.get("row_count", 0),
            descriptor.get("column_count", 0),
        )
    except Exception as exc:
        r.errors.append(str(exc))
        logger.error("[pipeline] Stage 1 FAILED for '%s': %s", file_path, exc)
    finally:
        r.duration_s = round(time.perf_counter() - t0, 2)

    return r


def _run_stage_2(
    file_path: str,
    local_mode: bool,
    schema_descriptor: Optional[dict],
    force: bool,
    canonical_key: str,
) -> StageResult:
    r = StageResult(stage=2, name="Format Normalization")

    # Idempotency check
    if not force and not local_mode and _s3_exists(canonical_key):
        logger.info("[pipeline] Stage 2 SKIPPED — artefact exists: %s", canonical_key)
        r.skipped = True
        r.success = True
        r.artifact_key = canonical_key
        return r

    if not isinstance(schema_descriptor, dict):
        r.errors.append("schema_descriptor unavailable — Stage 1 must succeed first")
        return r

    t0 = time.perf_counter()
    try:
        norm = normalize(file_path, schema_descriptor, local_mode=local_mode)
        r.artifact_key = norm.output_s3_key or canonical_key
        r.errors = norm.errors
        r.success = len(norm.errors) == 0 or norm.row_count > 0
        logger.info(
            "[pipeline] Stage 2 OK: %d rows, steps=%s",
            norm.row_count,
            norm.applied_steps,
        )
    except Exception as exc:
        r.errors.append(str(exc))
        logger.error("[pipeline] Stage 2 FAILED for '%s': %s", file_path, exc)
    finally:
        r.duration_s = round(time.perf_counter() - t0, 2)

    return r


def _run_stage_3(
    file_path: str,
    local_mode: bool,
    schema_descriptor: Optional[dict],
    force: bool,
    chunks_key: str,
    strategy: str,
    window_days: int,
    max_rows: int,
    overlap_rows: int,
) -> StageResult:
    r = StageResult(stage=3, name="Chunk & Serialize")

    # Idempotency check
    if not force and not local_mode and _s3_exists(chunks_key):
        logger.info("[pipeline] Stage 3 SKIPPED — artefact exists: %s", chunks_key)
        r.skipped = True
        r.success = True
        r.artifact_key = chunks_key
        return r

    if not isinstance(schema_descriptor, dict):
        r.errors.append("schema_descriptor unavailable — Stage 1 must succeed first")
        return r

    t0 = time.perf_counter()
    try:
        chunk_res = chunk_and_serialize(
            file_path=file_path,
            schema_descriptor=schema_descriptor,
            strategy=strategy,
            window_days=window_days,
            max_rows_per_chunk=max_rows,
            overlap_rows=overlap_rows,
            local_mode=local_mode,
        )
        r.artifact_key = chunk_res.output_s3_key or chunks_key
        r.errors = chunk_res.errors
        r.success = chunk_res.chunk_count > 0
        logger.info(
            "[pipeline] Stage 3 OK: %d chunks (%s strategy)",
            chunk_res.chunk_count,
            chunk_res.strategy_used,
        )
    except Exception as exc:
        r.errors.append(str(exc))
        logger.error("[pipeline] Stage 3 FAILED for '%s': %s", file_path, exc)
    finally:
        r.duration_s = round(time.perf_counter() - t0, 2)

    return r


def _run_stage_4(
    file_path: str,
    local_mode: bool,
    index_name: Optional[str],
    force: bool,
    chunks_key: str,
) -> StageResult:
    r = StageResult(stage=4, name="OpenSearch Ingest")

    # JSONL must exist (produced by Stage 3 or a previous run)
    if not local_mode and not _s3_exists(chunks_key):
        r.errors.append(f"chunks JSONL not found at '{chunks_key}' — run Stage 3 first")
        logger.error("[pipeline] Stage 4 aborted: no JSONL for '%s'", file_path)
        return r

    idx_name = index_name or settings.opensearch.index_name

    # Idempotency: delete existing documents for this source_file before re-indexing.
    # AOSS NextGen vector collections do not support custom _id, so delete-before-index
    # is the only way to prevent duplicate documents on repeated runs.
    if not force:
        _delete_existing_docs(file_path, idx_name)

    t0 = time.perf_counter()
    try:
        ingest_res = ingest_chunks(
            file_path=file_path,
            local_mode=local_mode,
            index_name=idx_name,
        )
        r.artifact_key = idx_name
        r.errors = ingest_res.errors
        r.success = ingest_res.documents_indexed > 0
        logger.info(
            "[pipeline] Stage 4 OK: %d indexed, %d failed → index=%s",
            ingest_res.documents_indexed,
            ingest_res.documents_failed,
            idx_name,
        )
    except Exception as exc:
        r.errors.append(str(exc))
        logger.error("[pipeline] Stage 4 FAILED for '%s': %s", file_path, exc)
    finally:
        r.duration_s = round(time.perf_counter() - t0, 2)

    return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _artifact_paths(file_path: str) -> dict[str, str]:
    """Compute the S3 keys for all three intermediate artefacts."""
    p = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    stem = p.stem
    pfx = settings.s3.prefix
    return {
        "schema": f"{pfx}vectorization/{folder}/schema/{stem}.schema.json",
        "canonical": f"{pfx}vectorization/{folder}/canonical/{stem}.canonical.ndjson",
        "chunks": f"{pfx}vectorization/{folder}/chunks/{stem}.chunks.jsonl",
    }


def _s3_exists(s3_key: str) -> bool:
    """Return True if the S3 object exists, False if 404, re-raise other errors."""
    s3 = boto3.client("s3", region_name=settings.s3.region)
    try:
        s3.head_object(Bucket=settings.s3.bucket_name, Key=s3_key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def _load_json_from_s3(s3_key: str) -> dict:
    s3 = boto3.client("s3", region_name=settings.s3.region)
    obj = s3.get_object(Bucket=settings.s3.bucket_name, Key=s3_key)
    return json.loads(obj["Body"].read())


def _try_load_schema(
    schema_key: str,
    local_mode: bool,
    file_path: str,
) -> Optional[dict]:
    """
    Try to load the schema_descriptor for a file when Stage 1 is not in the
    requested stages.  Returns None if the artefact does not exist.
    """
    if local_mode:
        p = PurePosixPath(file_path)
        folder = str(p.parent) or "root"
        local_path = (
            Path(settings.local_data_path)
            / "vectorization"
            / folder
            / "schema"
            / f"{p.stem}.schema.json"
        )
        if local_path.exists():
            return json.loads(local_path.read_bytes())
        logger.warning("[pipeline] Schema not found locally at %s", local_path)
        return None

    if _s3_exists(schema_key):
        return _load_json_from_s3(schema_key)

    logger.warning(
        "[pipeline] Schema not found at %s — Stages 2/3 will be skipped", schema_key
    )
    return None


def _delete_existing_docs(file_path: str, index_name: str) -> None:
    """
    Delete all documents in the index where source_file matches file_path.

    AOSS does not support _delete_by_query (it 404s unconditionally, regardless
    of index or document state), so matching docs are found via search and
    removed with a bulk delete-by-_id instead. Silently no-ops if the index
    does not exist yet or the query finds nothing.
    """
    try:
        client = _build_aoss_client()
        if not client.indices.exists(index=index_name):
            return
        resp = client.search(
            index=index_name,
            body={
                "query": {"term": {"source_file": file_path}},
                "_source": False,
                "size": 10_000,
            },
        )
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            return
        actions = [
            {"_op_type": "delete", "_index": index_name, "_id": hit["_id"]}
            for hit in hits
        ]
        deleted, errors = os_bulk(
            client, actions, raise_on_error=False, raise_on_exception=False
        )
        if deleted:
            logger.info(
                "[pipeline] Deleted %d existing doc(s) for '%s' from index '%s'",
                deleted,
                file_path,
                index_name,
            )
        if errors:
            logger.warning(
                "[pipeline] %d delete error(s) for '%s'", len(errors), file_path
            )
    except Exception as exc:
        # Non-fatal: log and continue — worst case we accumulate duplicates.
        logger.warning(
            "[pipeline] delete failed for '%s': %s — continuing ingest",
            file_path,
            exc,
        )
