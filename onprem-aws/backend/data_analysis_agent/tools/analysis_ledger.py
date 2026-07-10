"""
analysis_ledger.py
==================
The processing ledger (control log) for analysis-result vectorization.

An append-only JSONL object in S3, one record per (client, source_file),
recording what has been vectorized into the analysis index and with what.
It is the authority for "what is already in OpenSearch" — cheaper and richer
than querying the vector index (which has server-generated IDs and can't be
looked up per source file).

Record shape
------------
    {
      "client_id": "C1",
      "source_file": "C1/fuel_management_events.csv",
      "content_signature": "\"9b2cf…-2\"",   # S3 ETag
      "signature_type": "etag",
      "report_processed_at": "2026-06-30T12:00:00Z",
      "embed_model_id": "cohere.embed-v4:0",
      "index": "minelogx-analysis-v1",
      "pipeline_version": "1",
      "doc_count": 48,
      "status": "indexed"                      # or "error"
    }

The latest record for a given (client_id, source_file) wins (append-only; we
fold to the last state on read).

Public API
----------
    load_ledger(s3_client=None) -> list[dict]
    latest_by_file(records) -> dict[(client_id, source_file) -> record]
    is_client_stale(client_id, files_with_sigs, records, pipeline_version) -> bool
    append_records(records_to_add, s3_client=None) -> None
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import boto3

from data_analysis_agent.config.settings import settings

logger = logging.getLogger(__name__)


def _s3(s3_client: Any = None):
    return s3_client or boto3.client("s3", region_name=settings.opensearch.aws_region)


def load_ledger(s3_client: Any = None) -> list[dict]:
    """Read all ledger records. Returns [] if the ledger doesn't exist yet."""
    cfg = settings.analysis_ingest
    s3 = _s3(s3_client)
    try:
        resp = s3.get_object(Bucket=cfg.ledger_bucket, Key=cfg.ledger_key)
    except Exception as exc:  # noqa: BLE001 — includes NoSuchKey on first run
        if "NoSuchKey" in type(exc).__name__ or "NoSuchKey" in str(exc):
            logger.info("[analysis_ledger] No ledger yet at s3://%s/%s", cfg.ledger_bucket, cfg.ledger_key)
            return []
        logger.warning("[analysis_ledger] Could not read ledger: %s", exc)
        return []

    body = resp["Body"].read().decode("utf-8")
    records: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("[analysis_ledger] Skipping malformed ledger line")
    return records


def latest_by_file(records: Iterable[dict]) -> dict[tuple[str, str], dict]:
    """Fold append-only records to the last state per (client_id, source_file)."""
    latest: dict[tuple[str, str], dict] = {}
    for r in records:
        key = (r.get("client_id", ""), r.get("source_file", ""))
        latest[key] = r  # later lines overwrite earlier ones
    return latest


def is_client_stale(
    client_id: str,
    files_with_sigs: dict[str, str],
    records: Iterable[dict],
    pipeline_version: str,
) -> bool:
    """Decide whether a client needs (re)ingestion.

    Stale (returns True) when ANY of the client's current source files is:
      * not present in the ledger, OR
      * present with a different content_signature (ETag changed), OR
      * present but recorded under an older pipeline_version, OR
      * present but its last status is not "indexed".
    A client with no source files is not stale (nothing to do).

    Args:
        files_with_sigs: {source_file: etag} for the client's current S3 files.
    """
    if not files_with_sigs:
        return False

    latest = latest_by_file(records)
    for source_file, signature in files_with_sigs.items():
        rec = latest.get((client_id, source_file))
        if rec is None:
            return True
        if rec.get("content_signature") != signature:
            return True
        if str(rec.get("pipeline_version")) != str(pipeline_version):
            return True
        if rec.get("status") != "indexed":
            return True
    return False


def append_records(records_to_add: list[dict], s3_client: Any = None) -> None:
    """Append records to the ledger (read-modify-write of the JSONL object).

    AOSS/S3 has no native append; we read the current object and rewrite it
    with the new lines added. Callers should batch a client's files into one
    call to minimize rewrites.
    """
    if not records_to_add:
        return
    cfg = settings.analysis_ingest
    s3 = _s3(s3_client)

    existing = load_ledger(s3)
    all_records = existing + records_to_add
    body = "\n".join(json.dumps(r, default=str) for r in all_records) + "\n"
    s3.put_object(
        Bucket=cfg.ledger_bucket,
        Key=cfg.ledger_key,
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
    )
    logger.info(
        "[analysis_ledger] Appended %d record(s) → s3://%s/%s (%d total)",
        len(records_to_add),
        cfg.ledger_bucket,
        cfg.ledger_key,
        len(all_records),
    )
