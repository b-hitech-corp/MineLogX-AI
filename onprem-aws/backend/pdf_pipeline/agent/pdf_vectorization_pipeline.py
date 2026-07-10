"""
pdf_vectorization_pipeline.py
==============================
Top-level orchestrator for the PDF Vectorization Pipeline.

Coordinates all pipeline modules into a single run_pipeline(bucket, key) call.
The Lambda entrypoint lives in pdf_pipeline/lambda_function.py, which parses
the EventBridge event and delegates here via run_pipeline().

Full orchestration flow
-----------------------
1. Classify document (pdf_classifier → ClassificationResult)
   ├── doc_class = "simple"
   │     └── Extract via Textract (pdf_textract_extractor)
   └── doc_class = "complex_legal"
         ├── file ≤ thresholds → single Claude Sonnet call (pdf_claude_extractor)
         └── file > thresholds → section scan (pdf_section_scanner)
                                  → sequential mini-batch Claude calls
                                  → merge all batch outputs

2. Normalize all raw sections (pdf_normalizer → list[SectionRecord])

3. Embed all sections (pdf_titan_embedder → list[tuple[SectionRecord, vector]])

4. Ingest into OpenSearch AOSS (pdf_opensearch_ingestor → IngestResult)

5. Return PdfPipelineResult

Public API
----------
run_pipeline(bucket, key, config) → PdfPipelineResult
batch_run_pipeline(items, config) → list[PdfPipelineResult]

Environment variables (read by lambda_function.py and forwarded via PdfPipelineConfig)
---------------------------------------------------------------------------------------
AWS_REGION            — default "us-east-1"
OPENSEARCH_HOST       — required: AOSS collection endpoint
PDF_OPENSEARCH_INDEX  — default "pdf_legal_vecs"
PDF_ARTIFACT_BUCKET   — optional: S3 bucket for intermediate artifacts
PDF_CLAUDE_MODEL_ID   — optional: override Claude Sonnet model ID
PDF_HAIKU_MODEL_ID    — optional: override Claude Haiku model ID
PDF_TITAN_MODEL_ID    — optional: override Titan Embed model ID
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.pdf_classifier import classify, ClassificationResult
from pdf_pipeline.tools.pdf_claude_extractor import (
    MaxTokensTruncationError,
    PageLimitExceededError,
    build_carry_over_context,
    extract_with_claude,
)
from pdf_pipeline.tools.pdf_normalizer import (
    SectionRecord,
    build_section_metadata,
    normalize_sections,
)
from pdf_pipeline.tools.pdf_opensearch_ingestor import (
    IngestResult,
    build_opensearch_client,
    ingest_sections,
)
from pdf_pipeline.tools.pdf_section_scanner import (
    build_batches,
    scan_section_boundaries,
)
from pdf_pipeline.tools.pdf_textract_extractor import extract_with_textract
from pdf_pipeline.tools.pdf_titan_embedder import embed_sections_batch

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Pipeline result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PdfPipelineResult:
    file_key: str
    doc_class: str
    extraction_method: str  # "textract" | "claude_native" | "claude_batch"
    classification_signal: str  # "heuristic" | "s3_tag" | "haiku"
    sections_extracted: int
    sections_normalized: int
    sections_embedded: int
    sections_indexed: int
    sections_failed: int
    sections_skipped: int
    total_pages: int
    file_size_bytes: int
    batches_used: int  # 1 for single call / Textract; N for mini-batch
    input_tokens: int  # total Claude tokens across all batches (0 for Textract)
    output_tokens: int
    duration_s: float
    errors: list[str] = field(default_factory=list)

    @property
    def overall_success(self) -> bool:
        return self.sections_indexed > 0 and not self.errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _download_pdf(bucket: str, key: str, s3_client: Any) -> bytes:
    """Download a PDF from S3 into memory."""
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    data = resp["Body"].read()
    logger.info(
        "Downloaded s3://%s/%s (%.2f MB)", bucket, key, len(data) / (1024 * 1024)
    )
    return data


def _exceeds_single_call_threshold(
    file_size_bytes: int,
    page_count: int,
    config: PdfPipelineConfig,
) -> bool:
    size_mb = file_size_bytes / (1024 * 1024)
    return page_count > config.claude_max_pages or size_mb > config.claude_max_mb


# ---------------------------------------------------------------------------
# Extraction sub-paths
# ---------------------------------------------------------------------------


def _run_textract_path(
    bucket: str,
    key: str,
    classification: ClassificationResult,
    config: PdfPipelineConfig,
    textract_client: Any,
) -> tuple[list[dict], str, int, int, int, list[str]]:
    """Run the Textract extraction path.

    Returns (raw_sections, extraction_method, batches_used, input_tokens, output_tokens, errors).
    """
    result = extract_with_textract(
        bucket=bucket,
        key=key,
        file_size_bytes=classification.file_size_bytes,
        total_pages=classification.page_count,
        config=config,
        textract_client=textract_client,
    )
    return result.raw_sections, "textract", 1, 0, 0, result.errors


def _run_claude_single_path(
    pdf_bytes: bytes,
    bucket: str,
    key: str,
    classification: ClassificationResult,
    config: PdfPipelineConfig,
    bedrock_client: Any,
    deadline_ts: float | None = None,
) -> tuple[list[dict], str, int, int, int, list[str]]:
    """Run the Claude single-call path (≤550 pages, ≤18MB).

    Returns (raw_sections, extraction_method, batches_used, input_tokens, output_tokens, errors).
    """
    result = extract_with_claude(
        pdf_bytes=pdf_bytes,
        bucket=bucket,
        key=key,
        file_size_bytes=classification.file_size_bytes,
        total_pages=classification.page_count,
        config=config,
        bedrock_client=bedrock_client,
        page_start_offset=1,
        batch_index=0,
        context_note="",
        deadline_ts=deadline_ts,
    )
    return (
        result.raw_sections,
        "claude_native",
        1,
        result.input_tokens,
        result.output_tokens,
        result.errors,
    )


def _run_claude_minibatch_path(
    pdf_bytes: bytes,
    bucket: str,
    key: str,
    classification: ClassificationResult,
    config: PdfPipelineConfig,
    bedrock_client: Any,
    deadline_ts: float | None = None,
) -> tuple[list[dict], str, int, int, int, list[str]]:
    """Run the Claude mini-batch path (>550 pages OR >18MB).

    Steps:
      1. Scan section boundaries with pdfplumber.
      2. Slice into batches respecting section boundaries.
      3. Call Claude sequentially per batch with carry-over context.
      4. Merge all batch outputs in order.

    Returns (raw_sections, extraction_method, batches_used, input_tokens, output_tokens, errors).
    """
    logger.info("Mini-batch path: scanning section boundaries...")
    section_map = scan_section_boundaries(pdf_bytes, config)

    logger.info("Building batches from %d boundaries...", len(section_map.boundaries))
    batches = build_batches(section_map, pdf_bytes, config)
    logger.info("Built %d batches", len(batches))

    all_raw_sections: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    batch_errors: list[str] = []
    context_note = ""

    for batch_slice in batches:
        logger.info(
            "Processing batch %d/%d | pages %d–%d | %.2f MB",
            batch_slice.batch_index + 1,
            len(batches),
            batch_slice.page_start,
            batch_slice.page_end,
            batch_slice.size_mb,
        )

        try:
            result = extract_with_claude(
                pdf_bytes=batch_slice.pdf_bytes,
                bucket=bucket,
                key=key,
                file_size_bytes=classification.file_size_bytes,
                total_pages=classification.page_count,
                config=config,
                bedrock_client=bedrock_client,
                page_start_offset=batch_slice.page_start,
                batch_index=batch_slice.batch_index,
                context_note=context_note,
                deadline_ts=deadline_ts,
            )
        except MaxTokensTruncationError as exc:
            error_msg = f"Batch {batch_slice.batch_index} truncated at max_tokens: {exc}"
            logger.error(error_msg)
            batch_errors.append(error_msg)
            # Can't build carry-over from a batch that produced no sections —
            # continue processing remaining batches (partial indexing beats none).
            context_note = ""
            continue
        except PageLimitExceededError as exc:
            error_msg = f"Batch {batch_slice.batch_index} exceeded Bedrock page limit: {exc}"
            logger.error(error_msg)
            batch_errors.append(error_msg)
            # Same handling as truncation: record, skip this batch, keep going.
            context_note = ""
            continue

        if result.errors:
            logger.error("Batch %d failed: %s", batch_slice.batch_index, result.errors)
            batch_errors.extend(result.errors)
            # Continue processing remaining batches — partial indexing is better than none

        all_raw_sections.extend(result.raw_sections)
        total_input_tokens += result.input_tokens
        total_output_tokens += result.output_tokens

        # Build carry-over for next batch
        if result.raw_sections:
            context_note = build_carry_over_context(result.raw_sections[-1])
        else:
            context_note = ""

    return (
        all_raw_sections,
        "claude_batch",
        len(batches),
        total_input_tokens,
        total_output_tokens,
        batch_errors,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pipeline(
    bucket: str,
    key: str,
    config: PdfPipelineConfig | None = None,
    force: bool = False,
    s3_client: Any = None,
    textract_client: Any = None,
    bedrock_client: Any = None,
    bedrock_runtime_client: Any = None,
    opensearch_client: Any = None,
    deadline_ts: float | None = None,
) -> PdfPipelineResult:
    """Run the full PDF vectorization pipeline for a single document.

    Args:
        bucket: S3 bucket containing the PDF.
        key: S3 key of the PDF file.
        config: PdfPipelineConfig. Created from environment variables if None.
        force: If True, re-index even if the document is already indexed.
        s3_client: Reusable boto3 S3 client.
        textract_client: Reusable boto3 Textract client.
        bedrock_client: Reusable boto3 bedrock-runtime client for Claude/Haiku.
        bedrock_runtime_client: Reusable boto3 bedrock-runtime client for Titan Embed.
            If None, bedrock_client is reused (same service).
        opensearch_client: Reusable OpenSearch client.
        deadline_ts: Optional Lambda invocation deadline (time.time()-based),
            forwarded to the Claude extraction paths to bound their read timeout.

    Returns:
        PdfPipelineResult with complete execution metrics.
    """
    cfg = config or PdfPipelineConfig()
    start_time = time.time()
    errors: list[str] = []

    # Build AWS clients once
    s3 = s3_client or boto3.client("s3", region_name=cfg.aws_region)
    textract = textract_client or boto3.client("textract", region_name=cfg.aws_region)
    bedrock = bedrock_client or boto3.client(
        "bedrock-runtime", region_name=cfg.aws_region
    )
    # Dedicated client for Claude extraction only — separate Config (longer
    # read timeout, single app-owned retry attempt) from the shared client
    # used for Haiku classification and Titan embedding above/below.
    bedrock_claude = bedrock_client or boto3.client(
        "bedrock-runtime",
        region_name=cfg.aws_region,
        config=BotoConfig(
            connect_timeout=cfg.bedrock_connect_timeout_s,
            read_timeout=cfg.bedrock_read_timeout_s,
            retries={"max_attempts": 1},
            max_pool_connections=cfg.bedrock_max_pool_connections,
        ),
    )
    bedrock_rt = (
        bedrock_runtime_client or bedrock
    )  # Titan and Claude share the same service
    os_client = opensearch_client or build_opensearch_client(cfg)

    logger.info("=== PDF Pipeline START: s3://%s/%s ===", bucket, key)

    # -----------------------------------------------------------------------
    # Step 1: Classify
    # -----------------------------------------------------------------------
    try:
        classification = classify(
            bucket=bucket,
            key=key,
            config=cfg,
            s3_client=s3,
            bedrock_client=bedrock,
        )
        logger.info(
            "Classification: %s (signal=%s, confidence=%.2f, pages≈%d, size=%.2f MB)",
            classification.doc_class,
            classification.signal_used,
            classification.confidence,
            classification.page_count,
            classification.file_size_bytes / (1024 * 1024),
        )
    except Exception as exc:
        error_msg = f"Classification failed: {exc}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return PdfPipelineResult(
            file_key=key,
            doc_class="unknown",
            extraction_method="none",
            classification_signal="none",
            sections_extracted=0,
            sections_normalized=0,
            sections_embedded=0,
            sections_indexed=0,
            sections_failed=0,
            sections_skipped=0,
            total_pages=0,
            file_size_bytes=0,
            batches_used=0,
            input_tokens=0,
            output_tokens=0,
            duration_s=time.time() - start_time,
            errors=errors,
        )

    # -----------------------------------------------------------------------
    # Step 2: Extract
    # -----------------------------------------------------------------------
    raw_sections: list[dict] = []
    extraction_method = "none"
    batches_used = 0
    input_tokens = 0
    output_tokens = 0

    try:
        if classification.doc_class == "simple":
            (
                raw_sections,
                extraction_method,
                batches_used,
                input_tokens,
                output_tokens,
                path_errors,
            ) = _run_textract_path(
                bucket=bucket,
                key=key,
                classification=classification,
                config=cfg,
                textract_client=textract,
            )
        else:
            # complex_legal — check whether it fits in a single call
            if not _exceeds_single_call_threshold(
                classification.file_size_bytes, classification.page_count, cfg
            ):
                pdf_bytes = _download_pdf(bucket, key, s3)
                (
                    raw_sections,
                    extraction_method,
                    batches_used,
                    input_tokens,
                    output_tokens,
                    path_errors,
                ) = _run_claude_single_path(
                    pdf_bytes=pdf_bytes,
                    bucket=bucket,
                    key=key,
                    classification=classification,
                    config=cfg,
                    bedrock_client=bedrock_claude,
                    deadline_ts=deadline_ts,
                )
            else:
                pdf_bytes = _download_pdf(bucket, key, s3)
                (
                    raw_sections,
                    extraction_method,
                    batches_used,
                    input_tokens,
                    output_tokens,
                    path_errors,
                ) = _run_claude_minibatch_path(
                    pdf_bytes=pdf_bytes,
                    bucket=bucket,
                    key=key,
                    classification=classification,
                    config=cfg,
                    bedrock_client=bedrock_claude,
                    deadline_ts=deadline_ts,
                )

        errors.extend(path_errors)

        logger.info(
            "Extraction complete: %d raw sections | method=%s | batches=%d",
            len(raw_sections),
            extraction_method,
            batches_used,
        )

    except Exception as exc:
        error_msg = f"Extraction failed ({extraction_method}): {exc}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)

    if not raw_sections:
        logger.warning("No sections extracted — pipeline cannot continue")
        return PdfPipelineResult(
            file_key=key,
            doc_class=classification.doc_class,
            extraction_method=extraction_method,
            classification_signal=classification.signal_used,
            sections_extracted=0,
            sections_normalized=0,
            sections_embedded=0,
            sections_indexed=0,
            sections_failed=0,
            sections_skipped=0,
            total_pages=classification.page_count,
            file_size_bytes=classification.file_size_bytes,
            batches_used=batches_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=time.time() - start_time,
            errors=errors or ["No sections extracted"],
        )

    # -----------------------------------------------------------------------
    # Step 3: Normalize
    # -----------------------------------------------------------------------
    metadata = build_section_metadata(
        bucket=bucket,
        key=key,
        doc_class=classification.doc_class,
        file_size_bytes=classification.file_size_bytes,
        total_pages=classification.page_count,
    )

    try:
        sections: list[SectionRecord] = normalize_sections(
            raw_sections=raw_sections,
            extraction_method=extraction_method,
            metadata=metadata,
            config=cfg,
        )
        logger.info("Normalized %d sections", len(sections))
    except Exception as exc:
        error_msg = f"Normalization failed: {exc}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        sections = []

    if not sections:
        return PdfPipelineResult(
            file_key=key,
            doc_class=classification.doc_class,
            extraction_method=extraction_method,
            classification_signal=classification.signal_used,
            sections_extracted=len(raw_sections),
            sections_normalized=0,
            sections_embedded=0,
            sections_indexed=0,
            sections_failed=len(raw_sections),
            sections_skipped=0,
            total_pages=classification.page_count,
            file_size_bytes=classification.file_size_bytes,
            batches_used=batches_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=time.time() - start_time,
            errors=errors or ["Normalization produced 0 sections"],
        )

    # -----------------------------------------------------------------------
    # Step 4: Embed
    # -----------------------------------------------------------------------
    embedded: list[tuple[SectionRecord, list[float]]] = []
    try:
        embedded = embed_sections_batch(
            sections=sections,
            config=cfg,
            bedrock_runtime_client=bedrock_rt,
        )
        logger.info("Embedded %d/%d sections", len(embedded), len(sections))
    except Exception as exc:
        error_msg = f"Embedding failed: {exc}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)

    embed_failed = len(sections) - len(embedded)
    if embed_failed > 0:
        errors.append(f"{embed_failed} sections failed to embed")

    # -----------------------------------------------------------------------
    # Step 5: Ingest
    # -----------------------------------------------------------------------
    ingest_result = IngestResult(
        index_name=cfg.opensearch_index,
        documents_indexed=0,
        documents_failed=0,
        documents_skipped=0,
    )

    if embedded:
        try:
            ingest_result = ingest_sections(
                sections_with_embeddings=embedded,
                config=cfg,
                opensearch_client=os_client,
                force=force,
            )
            errors.extend(ingest_result.errors)
            logger.info(
                "Ingested %d sections | failed=%d | skipped=%d",
                ingest_result.documents_indexed,
                ingest_result.documents_failed,
                ingest_result.documents_skipped,
            )
        except Exception as exc:
            error_msg = f"Ingestion failed: {exc}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)

    duration = time.time() - start_time
    logger.info(
        "=== PDF Pipeline DONE: %s | %.1fs | %d indexed | %d errors ===",
        key,
        duration,
        ingest_result.documents_indexed,
        len(errors),
    )

    return PdfPipelineResult(
        file_key=key,
        doc_class=classification.doc_class,
        extraction_method=extraction_method,
        classification_signal=classification.signal_used,
        sections_extracted=len(raw_sections),
        sections_normalized=len(sections),
        sections_embedded=len(embedded),
        sections_indexed=ingest_result.documents_indexed,
        sections_failed=embed_failed + ingest_result.documents_failed,
        sections_skipped=ingest_result.documents_skipped,
        total_pages=classification.page_count,
        file_size_bytes=classification.file_size_bytes,
        batches_used=batches_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_s=duration,
        errors=errors,
    )


def batch_run_pipeline(
    bucket: str,
    folders: list[str],
    config: PdfPipelineConfig | None = None,
    force: bool = False,
) -> list[PdfPipelineResult]:
    """Process all PDFs found in a list of S3 folder prefixes.

    Args:
        bucket: S3 bucket to scan.
        folders: List of S3 folder prefixes (e.g. ["legal/", "regulations/"]).
        config: PdfPipelineConfig.
        force: If True, re-index all documents regardless of prior indexing.

    Returns:
        List of PdfPipelineResult, one per PDF processed.
    """
    cfg = config or PdfPipelineConfig()
    s3 = boto3.client("s3", region_name=cfg.aws_region)

    all_results: list[PdfPipelineResult] = []

    for folder in folders:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=folder, Delimiter="/")
        pdf_keys = [
            obj["Key"]
            for obj in resp.get("Contents", [])
            if obj["Key"].lower().endswith(".pdf") and obj["Key"] != folder
        ]
        logger.info("Found %d PDFs in s3://%s/%s", len(pdf_keys), bucket, folder)

        for key in pdf_keys:
            result = run_pipeline(bucket=bucket, key=key, config=cfg, force=force)
            all_results.append(result)

    successful = sum(1 for r in all_results if r.overall_success)
    logger.info(
        "Batch complete: %d/%d PDFs successfully indexed",
        successful,
        len(all_results),
    )
    return all_results
