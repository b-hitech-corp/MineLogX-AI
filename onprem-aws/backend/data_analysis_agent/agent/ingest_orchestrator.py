"""
ingest_orchestrator.py
======================
Ledger-gated orchestration of data-analysis vectorization.

Ties together the pieces: enumerate a client's source files + ETags → ask the
ledger whether the client is stale → if so, run FolderPipeline, replace the
client's docs in the analysis index, and update the ledger.

Compute (FolderPipeline) and ingestion (analysis_ingestor) stay decoupled;
this module is the only thing that knows about both plus the ledger.

Public API
----------
    ingest_client(client_id, *, force=False, local_mode=False) -> dict
    ingest_all(clients=None, *, force=False, local_mode=False) -> list[dict]

Both are invoked by the Fabric `analysis.*` tasks.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Optional

import boto3

from data_analysis_agent.agent.pipeline import FolderPipeline
from data_analysis_agent.config.settings import settings
from data_analysis_agent.tools import analysis_ledger
from data_analysis_agent.tools.analysis_ingestor import replace_client
from data_analysis_agent.tools.analysis_report_serializer import render_report_chunks
from data_analysis_agent.tools.s3_browser import list_folder

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# S3 enumeration with ETags (the change-detection signature)
# ---------------------------------------------------------------------------


def _list_files_with_etags(client_id: str, s3_client: Any = None) -> dict[str, str]:
    """Return {source_file: etag} for a client's CSVs.

    ETag comes back with list_objects_v2 (no extra call). Keys are returned
    relative to the configured prefix, matching s3_browser / the report's
    overview.files paths (e.g. "C1/fuel_management_events.csv").
    """
    s3 = s3_client or boto3.client("s3", region_name=settings.s3.region)
    prefix = (settings.s3.prefix + client_id).rstrip("/") + "/"
    paginator = s3.get_paginator("list_objects_v2")
    out: dict[str, str] = {}
    for page in paginator.paginate(Bucket=settings.s3.bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(".csv") or key.endswith("/"):
                continue
            relative = key[len(settings.s3.prefix):]
            out[relative] = obj.get("ETag", "")
    return out


def _representative_signature(files_with_etags: dict[str, str]) -> str:
    """A single stamp for the client's inputs (per-file ETags live in the ledger)."""
    return ";".join(f"{k}={v}" for k, v in sorted(files_with_etags.items()))


# ---------------------------------------------------------------------------
# Per-client ingest
# ---------------------------------------------------------------------------


def ingest_client(
    client_id: str,
    *,
    force: bool = False,
    local_mode: bool = False,
    s3_client: Any = None,
) -> dict:
    """(Re)vectorize one client's analysis results if the ledger says it's stale.

    Returns a status dict: {client_id, action, ...} where action is one of
    "skipped" | "indexed" | "error" | "no_files".
    """
    cfg = settings.analysis_ingest

    if local_mode:
        # Local runs can't read S3 ETags; treat every file as present-but-unsigned.
        files = list_folder(client_id, local_mode=True)
        files_with_etags = {f: "local" for f in files}
    else:
        files_with_etags = _list_files_with_etags(client_id, s3_client)

    if not files_with_etags:
        logger.info("[ingest] client=%s: no source files", client_id)
        return {"client_id": client_id, "action": "no_files"}

    records = analysis_ledger.load_ledger(s3_client)
    stale = analysis_ledger.is_client_stale(
        client_id, files_with_etags, records, cfg.pipeline_version
    )
    if not stale and not force:
        logger.info("[ingest] client=%s: up to date — skipping", client_id)
        return {
            "client_id": client_id,
            "action": "skipped",
            "files": len(files_with_etags),
        }

    logger.info(
        "[ingest] client=%s: %s → running pipeline + ingest",
        client_id,
        "forced" if force else "stale",
    )

    # 1) Compute the report.
    report = FolderPipeline(local_mode=local_mode).run(client_id)

    # 2) Render parent+child chunks.
    signature = _representative_signature(files_with_etags)
    chunks = render_report_chunks(
        report,
        content_signature=signature,
        embed_model_id=settings.opensearch.embedding_model_id,
        pipeline_version=cfg.pipeline_version,
    )
    if not chunks:
        logger.warning("[ingest] client=%s: report produced no chunks", client_id)
        return {"client_id": client_id, "action": "error", "error": "no chunks rendered"}

    # 3) Replace the client's docs in the analysis index.
    result = replace_client(client_id, chunks)

    status = "indexed" if result.documents_indexed > 0 and not result.errors else (
        "error" if result.errors and result.documents_indexed == 0 else "indexed"
    )

    # 4) Update the ledger — one record per source file (only on success).
    if status == "indexed":
        processed_at = report.get("processed_at", "")
        ledger_records = [
            {
                "client_id": client_id,
                "source_file": source_file,
                "content_signature": etag,
                "signature_type": "etag",
                "report_processed_at": processed_at,
                "embed_model_id": settings.opensearch.embedding_model_id,
                "index": result.index_name,
                "pipeline_version": cfg.pipeline_version,
                "doc_count": result.documents_indexed,
                "status": "indexed",
            }
            for source_file, etag in files_with_etags.items()
        ]
        analysis_ledger.append_records(ledger_records, s3_client)

    return {
        "client_id": client_id,
        "action": status,
        "indexed": result.documents_indexed,
        "deleted": result.documents_deleted,
        "failed": result.documents_failed,
        "errors": result.errors,
    }


def ingest_all(
    clients: Optional[list[str]] = None,
    *,
    force: bool = False,
    local_mode: bool = False,
    s3_client: Any = None,
) -> list[dict]:
    """Ingest every given client (or all discovered top-level client folders).

    When `clients` is None, discovers client folders from the telemetry bucket's
    top-level prefixes (S3 mode only).
    """
    if clients is None:
        clients = _discover_clients(local_mode=local_mode, s3_client=s3_client)
    logger.info("[ingest] processing %d client(s): %s", len(clients), clients)
    return [
        ingest_client(c, force=force, local_mode=local_mode, s3_client=s3_client)
        for c in clients
    ]


def _discover_clients(local_mode: bool, s3_client: Any = None) -> list[str]:
    """List top-level client folders (e.g. C1, C2) under the telemetry prefix."""
    if local_mode:
        from pathlib import Path

        base = Path(settings.local_data_path)
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []

    s3 = s3_client or boto3.client("s3", region_name=settings.s3.region)
    prefix = settings.s3.prefix
    resp = s3.list_objects_v2(Bucket=settings.s3.bucket_name, Prefix=prefix, Delimiter="/")
    clients: list[str] = []
    for cp in resp.get("CommonPrefixes", []):
        name = cp.get("Prefix", "")[len(prefix):].rstrip("/")
        if name:
            clients.append(name)
    return sorted(clients)


# ---------------------------------------------------------------------------
# CLI entrypoint — manual / onboarding trigger
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run analysis vectorization from the command line (manual/onboarding run).

    Examples
    --------
        python -m data_analysis_agent.agent.ingest_orchestrator --client C1
        python -m data_analysis_agent.agent.ingest_orchestrator --all --force
        python -m data_analysis_agent.agent.ingest_orchestrator --client C1 --local

    Environment (read via settings): OPENSEARCH_HOST (target AOSS endpoint),
    FLEET_S3_BUCKET (telemetry + ledger bucket), AWS creds for the target
    account. ANALYSIS_INDEX defaults to "analysis_vecs".

    Exit code is non-zero if any client errored, so callers/CI can detect failure.
    """
    parser = argparse.ArgumentParser(
        prog="ingest_orchestrator",
        description=(
            "Vectorize data-analysis results into the analysis OpenSearch index "
            "(ledger-gated; already-ingested, unchanged clients are skipped)."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--client", help="Single client folder to ingest, e.g. C1")
    target.add_argument(
        "--all", action="store_true", help="Ingest all discovered client folders"
    )
    parser.add_argument(
        "--clients",
        help="Comma-separated subset to use with --all, e.g. C1,C2 (default: discover all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if the ledger says the client is up to date",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Read sample_data/ instead of S3 (dry run; no ETag change-detection)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    if args.client:
        results = [ingest_client(args.client, force=args.force, local_mode=args.local)]
    else:
        subset = args.clients.split(",") if args.clients else None
        results = ingest_all(clients=subset, force=args.force, local_mode=args.local)

    print(json.dumps(results, indent=2, default=str))
    return 1 if any(r.get("action") == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
