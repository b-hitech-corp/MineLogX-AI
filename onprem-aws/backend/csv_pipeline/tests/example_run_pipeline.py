"""
example_run_pipeline.py — How to drive the CSV Vectorization Pipeline
=====================================================================
A runnable, copy-pasteable example of calling the CSV Vectorization Pipeline
(`csv_pipeline.agent.csv_vectorization_pipeline.run_pipeline`).

The pipeline runs four stages for a single CSV file:
    1. Schema Inspection    → schema_descriptor.json (S3)
    2. Format Normalization → canonical.ndjson       (S3)
    3. Chunk & Serialize    → chunks.jsonl           (S3)
    4. OpenSearch Ingest    → AOSS vector index (csv_telemetry_vecs)

Run it
------
Always run from the REPO ROOT (the parent of csv_pipeline/) so the
fully-qualified package imports resolve, e.g.:

    cd <repo-root>          # the folder that contains csv_pipeline/
    python -m csv_pipeline.tests.example_run_pipeline C1/fuel_management_events.csv

    # only the first three stages (no OpenSearch cluster needed):
    python -m csv_pipeline.tests.example_run_pipeline C1/fuel_management_events.csv --stages 1 2 3

    # force a full re-run even if artefacts already exist in S3:
    python -m csv_pipeline.tests.example_run_pipeline C1/fuel_management_events.csv --force

    # read/write artefacts locally from sample_data/ (Stage 4 still hits AOSS):
    python -m csv_pipeline.tests.example_run_pipeline C1/fuel_management_events.csv --local

Prerequisites (the pipeline talks to real AWS services)
-------------------------------------------------------
  - AWS credentials available (env vars, ~/.aws/credentials, or an IAM role).
  - Bedrock model access for the configured model (settings.bedrock.model_id).
  - For Stage 4: OPENSEARCH_HOST set to the AOSS collection endpoint and the
    execution role granted aoss:APIAccessAll on the collection.
  - The source CSV present at the given S3 key (or under sample_data/ with --local).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Make the repo root importable when this file is run directly (python path/to/file).
# Running via `python -m csv_pipeline.tests.example_run_pipeline` makes this a no-op.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from csv_pipeline.agent.csv_vectorization_pipeline import PipelineResult, run_pipeline


def run_example(
    file_path: str,
    stages: list[int] | None = None,
    force: bool = False,
    local_mode: bool = False,
    backend: str = "bedrock",
) -> PipelineResult:
    """Invoke the pipeline once and return its PipelineResult.

    Args:
        file_path:  S3 key (e.g. "C1/fuel_management_events.csv"), or a path
                    relative to sample_data/ when local_mode=True.
        stages:     Subset of [1, 2, 3, 4] to run. None runs all four.
        force:      Re-run every stage even if its artefact already exists.
        local_mode: Read/write artefacts from sample_data/ on disk.
        backend:    LLM backend for Stage 1 — "bedrock" (default) or "ollama".
    """
    result = run_pipeline(
        file_path=file_path,
        stages=stages,
        force=force,
        local_mode=local_mode,
        backend=backend,
        # Stage-3 chunking knobs (defaults shown for illustration):
        chunking_strategy="time_window",
        window_days=7,
        max_rows_per_chunk=500,
        overlap_rows=50,
    )

    # PipelineResult prints a readable per-stage summary via __str__.
    print(result)

    # Programmatic inspection of the outcome:
    print("\n--- programmatic view ---")
    print(f"overall_success : {result.overall_success}")
    print(f"failed_stages   : {result.failed_stages}")
    for r in result.stage_results:
        print(f"  stage {r.stage} {r.name:<22} {r.status_label:<8} "
              f"{r.duration_s:6.1f}s  artifact={r.artifact_key}")

    return result


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the CSV Vectorization Pipeline on one file.")
    p.add_argument("file_path", nargs="?", default="C1/fuel_management_events.csv",
                   help="S3 key of the CSV (or sample_data-relative path with --local).")
    p.add_argument("--stages", nargs="+", type=int, choices=[1, 2, 3, 4], default=None,
                   help="Subset of stages to run (default: all four).")
    p.add_argument("--force", action="store_true",
                   help="Re-run every stage even if artefacts already exist.")
    p.add_argument("--local", action="store_true",
                   help="Read/write artefacts from sample_data/ instead of S3.")
    p.add_argument("--backend", default="bedrock", choices=["bedrock", "ollama"],
                   help="LLM backend for Stage 1 (default: bedrock).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    result = run_example(
        file_path=args.file_path,
        stages=args.stages,
        force=args.force,
        local_mode=args.local,
        backend=args.backend,
    )
    return 0 if result.overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
