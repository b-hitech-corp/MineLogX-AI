"""
example_run_pipeline.py — How to drive the PDF Vectorization Pipeline
=====================================================================
A runnable example of calling the PDF Vectorization Pipeline
(`pdf_pipeline.agent.pdf_vectorization_pipeline.run_pipeline`) against real
legislation PDFs in S3, with per-step validation output.

Pipeline steps (all inside run_pipeline):
    1. Classify        3-signal cascade (heuristic -> S3 tag -> Haiku) -> simple | complex_legal
    2. Extract         Textract (simple) OR Claude native PDF (complex_legal, single or mini-batch)
    3. Normalize       raw sections -> clean SectionRecords
    4. Embed           Titan Embed v2 -> 1024-dim vectors
    5. Ingest          bulk into AOSS index `pdf_legal_vecs`

Source data (defaults baked in, override with flags):
    bucket = bhitech-minelogx-poc-legislation-documents
    prefix = docs/            (PDFs live under this folder)

Run it (from the REPO ROOT so the package imports resolve)
----------------------------------------------------------
    cd <repo-root>

    # 1) Just list the PDFs the pipeline would see (no AWS cost, no AOSS):
    python -m pdf_pipeline.tests.example_run_pipeline --list

    # 2) Validate ONLY classification on one file (cheap: heuristics + maybe one Haiku call;
    #    no extraction/embedding/ingest, so OPENSEARCH_HOST is not required):
    python -m pdf_pipeline.tests.example_run_pipeline docs/<file>.pdf --classify-only

    # 3) Full end-to-end on ONE file, with a per-step breakdown:
    OPENSEARCH_HOST=<id>.us-east-1.aoss.amazonaws.com AWS_REGION=us-east-1 \
      python -m pdf_pipeline.tests.example_run_pipeline docs/<file>.pdf

    # 4) Batch every PDF under the prefix:
    OPENSEARCH_HOST=... python -m pdf_pipeline.tests.example_run_pipeline --batch

If no key is given (and not --list), the first PDF found under the prefix is used.

Prerequisites (the full run talks to real AWS services)
-------------------------------------------------------
  - AWS credentials available (SageMaker role / env / ~/.aws).
  - Bedrock model access: Claude Haiku (classifier), Claude Sonnet (complex extraction),
    and Titan Embed v2 (`amazon.titan-embed-text-v2:0`).
  - Textract permissions (simple path): textract:StartDocumentAnalysis / GetDocumentAnalysis.
  - s3:GetObject + s3:GetObjectTagging on the source bucket.
  - For steps 4-5: OPENSEARCH_HOST set + the role authorized on the AOSS collection
    (aoss:APIAccessAll + data access policy). The `pdf_legal_vecs` index is auto-created.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Make the repo root importable when run directly; a no-op under `python -m`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import boto3

from pdf_pipeline.agent.pdf_vectorization_pipeline import (
    PdfPipelineResult,
    batch_run_pipeline,
    run_pipeline,
)
from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.pdf_classifier import classify

# --- Source data defaults (per the project's legislation bucket) -----------
DEFAULT_BUCKET = "bhitech-minelogx-poc-legislation-documents"
DEFAULT_PREFIX = "docs/"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_pdfs(bucket: str, prefix: str, s3=None) -> list[str]:
    """Return PDF keys under the prefix (paginated; matches what batch would scan)."""
    s3 = s3 or boto3.client("s3")
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if obj["Key"].lower().endswith(".pdf"):
                keys.append(obj["Key"])
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return keys


# ---------------------------------------------------------------------------
# Step 1 only — classification (no extraction / embedding / ingest)
# ---------------------------------------------------------------------------


def run_classify_only(bucket: str, key: str, config: PdfPipelineConfig) -> None:
    s3 = boto3.client("s3", region_name=config.aws_region)
    bedrock = boto3.client("bedrock-runtime", region_name=config.aws_region)
    result = classify(
        bucket=bucket, key=key, config=config, s3_client=s3, bedrock_client=bedrock
    )

    print(f"\n=== Classification: s3://{bucket}/{key} ===")
    print(f"  doc_class          : {result.doc_class}")
    print(f"  signal_used        : {result.signal_used}")
    print(f"  confidence         : {result.confidence:.2f}")
    print(f"  page_count (approx): {result.page_count}")
    print(f"  file_size          : {result.file_size_bytes / (1024 * 1024):.2f} MB")
    print(f"  avg_chars_per_page : {result.avg_chars_per_page:.0f}")
    print(f"  reasoning          : {result.reasoning}")
    print(
        "\n-> Routing: 'simple' uses Textract; 'complex_legal' uses Claude native PDF."
    )


# ---------------------------------------------------------------------------
# Full run — print a per-step breakdown of the result
# ---------------------------------------------------------------------------


def _print_result(r: PdfPipelineResult) -> None:
    print(f"\n=== Pipeline result: {r.file_key} ===")
    print(f"  overall_success : {r.overall_success}")
    print(
        f"  duration        : {r.duration_s:.1f}s   "
        f"pages≈{r.total_pages}  size={r.file_size_bytes / (1024 * 1024):.2f} MB"
    )
    print("  --- step-by-step ---")
    print(
        f"  1. classify   : doc_class={r.doc_class}  signal={r.classification_signal}"
    )
    print(
        f"  2. extract    : method={r.extraction_method}  sections={r.sections_extracted}  "
        f"batches={r.batches_used}  tokens(in/out)={r.input_tokens}/{r.output_tokens}"
    )
    print(f"  3. normalize  : sections={r.sections_normalized}")
    print(f"  4. embed      : sections={r.sections_embedded}")
    print(
        f"  5. ingest     : indexed={r.sections_indexed}  "
        f"failed={r.sections_failed}  skipped={r.sections_skipped}"
    )
    if r.errors:
        print("  errors:")
        for e in r.errors[:5]:
            print(f"    - {e}")
        if len(r.errors) > 5:
            print(f"    ... and {len(r.errors) - 5} more")


def run_one(
    bucket: str, key: str, config: PdfPipelineConfig, force: bool
) -> PdfPipelineResult:
    result = run_pipeline(bucket=bucket, key=key, config=config, force=force)
    _print_result(result)
    return result


def run_batch(
    bucket: str, prefix: str, config: PdfPipelineConfig, force: bool
) -> list[PdfPipelineResult]:
    results = batch_run_pipeline(
        bucket=bucket, folders=[prefix], config=config, force=force
    )
    print(
        f"\n=== Batch summary: {len(results)} PDF(s) under s3://{bucket}/{prefix} ==="
    )
    for r in results:
        flag = "OK " if r.overall_success else "FAIL"
        print(
            f"  [{flag}] {r.file_key}  ({r.doc_class}/{r.extraction_method}, "
            f"{r.sections_indexed} indexed, {len(r.errors)} err)"
        )
    ok = sum(1 for r in results if r.overall_success)
    print(f"\nOverall: {ok}/{len(results)} succeeded")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the PDF Vectorization Pipeline on real S3 PDFs."
    )
    p.add_argument(
        "key",
        nargs="?",
        default=None,
        help="S3 key of the PDF (e.g. docs/mining-act.pdf). "
        "If omitted, the first PDF under --prefix is used.",
    )
    p.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"S3 bucket (default: {DEFAULT_BUCKET}).",
    )
    p.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"S3 prefix/folder (default: {DEFAULT_PREFIX}).",
    )
    p.add_argument(
        "--list", action="store_true", help="List PDFs under the prefix and exit."
    )
    p.add_argument(
        "--classify-only",
        action="store_true",
        help="Run only step 1 (classification); no extraction/embedding/ingest.",
    )
    p.add_argument(
        "--batch", action="store_true", help="Process every PDF under the prefix."
    )
    p.add_argument(
        "--force", action="store_true", help="Re-index even if already indexed."
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    config = PdfPipelineConfig()

    # --list: enumerate and stop.
    if args.list:
        keys = list_pdfs(args.bucket, args.prefix)
        print(f"\n{len(keys)} PDF(s) under s3://{args.bucket}/{args.prefix}:")
        for k in keys:
            print(f"  {k}")
        return 0

    # --batch: process the whole prefix.
    if args.batch:
        results = run_batch(args.bucket, args.prefix, config, args.force)
        return 0 if results and all(r.overall_success for r in results) else 1

    # Single-file modes: resolve the key (first PDF under prefix if not given).
    key = args.key
    if key is None:
        keys = list_pdfs(args.bucket, args.prefix)
        if not keys:
            print(
                f"No PDFs found under s3://{args.bucket}/{args.prefix}", file=sys.stderr
            )
            return 1
        key = keys[0]
        print(f"No key given — using first PDF found: {key}")

    if args.classify_only:
        run_classify_only(args.bucket, key, config)
        return 0

    result = run_one(args.bucket, key, config, args.force)
    return 0 if result.overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
