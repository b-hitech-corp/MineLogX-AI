"""
pdf_classifier.py
=================
3-signal document classifier for the PDF Vectorization Pipeline.

Determines whether an incoming PDF should be routed to:
  - "simple"        → Amazon Textract (LAYOUT + TABLES)
  - "complex_legal" → Amazon Bedrock Claude Sonnet 4 (native PDF)

Signal cascade (short-circuits on first confident decision):
  Signal 1: Free heuristics — page count, avg chars/page, scanned detection.
            No API calls. Uses S3 HeadObject + PDF xref tail sample.
  Signal 2: S3 object tag — checks the "doc-type" tag on the S3 object.
            No model calls. Resolves in one API call.
  Signal 3: Claude Haiku — sends first-page text to Haiku with tool_choice
            for guaranteed structured output. ~$0.0003/call.
            If confidence < threshold → safe-default to complex_legal.

Public API
----------
    classify(bucket, key, config, s3_client, bedrock_client) -> ClassificationResult
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import boto3
import fitz  # PyMuPDF — used only for the first-page text sample

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    doc_class: str  # "complex_legal" | "simple"
    confidence: float  # 0.0–1.0
    signal_used: str  # "heuristic" | "s3_tag" | "haiku"
    page_count: int
    file_size_bytes: int
    avg_chars_per_page: float
    reasoning: str


# ---------------------------------------------------------------------------
# Claude Haiku tool definition
# ---------------------------------------------------------------------------

_CLASSIFY_TOOL = {
    "name": "classify_document",
    "description": (
        "Classify a regulatory PDF document based on its first page text. "
        "Use 'high' for dense legal/regulatory documents with numbered clauses, "
        "cross-references, definitions sections, or legislative language. "
        "Use 'low' or 'medium' for standard forms, templates, or lightly formatted documents."
    ),
    "inputSchema": {
        # Converse requires the schema wrapped under a "json" key (ToolInputSchema union).
        "json": {
            "type": "object",
            "properties": {
                "complexity": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": (
                        "high → dense regulatory/legal structure (mining acts, safety codes, "
                        "environmental regulations with numbered clauses); "
                        "medium → moderately structured document; "
                        "low → simple form, template, or administrative document"
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence in this classification, 0.0–1.0",
                },
                "reasoning": {
                    "type": "string",
                    "description": "One sentence explaining the classification decision",
                },
            },
            "required": ["complexity", "confidence", "reasoning"],
        },
    },
}

_CLASSIFY_PROMPT = (
    "You are classifying a PDF document for a mining regulatory RAG system.\n\n"
    "First page text:\n"
    "---\n"
    "{first_page_text}\n"
    "---\n\n"
    "Classify this document's structural complexity. "
    "Call the classify_document tool with your assessment."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_page_count(
    bucket: str,
    key: str,
    s3_client: Any,
    tail_bytes: int = 2048,
) -> int:
    """Estimate PDF page count by reading the xref count from the file tail.

    The PDF trailer dictionary at the end of the file contains /Size (total
    xref entries) and /Root. We parse /Count from the Pages object or fall
    back to counting '/Page' occurrences in the tail.

    Returns 0 if the estimate fails — the caller treats 0 as 'unknown'.
    """
    try:
        size_resp = s3_client.head_object(Bucket=bucket, Key=key)
        file_size = size_resp["ContentLength"]
        fetch_start = max(0, file_size - tail_bytes)
        range_header = f"bytes={fetch_start}-{file_size - 1}"
        tail_resp = s3_client.get_object(Bucket=bucket, Key=key, Range=range_header)
        tail_data = tail_resp["Body"].read()
        tail_text = tail_data.decode("latin-1", errors="replace")

        # Try to extract /Count from the trailer
        count_match = re.search(r"/Count\s+(\d+)", tail_text)
        if count_match:
            return int(count_match.group(1))

        # Fallback: count /Page occurrences (each page dictionary has /Type /Page)
        page_refs = len(re.findall(r"/Type\s*/Page\b", tail_text))
        if page_refs > 0:
            return page_refs

    except Exception:
        logger.debug(
            "Page count estimation failed for %s/%s", bucket, key, exc_info=True
        )

    return 0


def _fetch_first_page_text(
    bucket: str,
    key: str,
    s3_client: Any,
) -> str:
    """Return page-1 plain text by downloading the FULL PDF.

    A PDF cannot be parsed from a byte *prefix*: its cross-reference table lives
    at the END of the file and page content streams may sit anywhere, so a
    leading-range read yields a truncated, unparseable document and PyMuPDF
    extracts no text. We therefore fetch the whole object (these legislation
    documents are only a few MB) and let PyMuPDF resolve page 1.

    Returns "" on failure (an empty result then routes through the Haiku
    signal / safe default rather than crashing).
    """
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        data = resp["Body"].read()
        doc = fitz.open(stream=data, filetype="pdf")
        if len(doc) == 0:
            return ""
        text = doc[0].get_text("text")
        doc.close()
        return text
    except Exception:
        logger.debug(
            "First-page text extraction failed for %s/%s", bucket, key, exc_info=True
        )
        return ""


def _classify_with_haiku(
    first_page_text: str,
    page_count: int,
    file_size_bytes: int,
    avg_chars: float,
    config: PdfPipelineConfig,
    bedrock_client: Any,
) -> ClassificationResult:
    """Call Claude Haiku with tool_choice for guaranteed structured classification."""
    if not first_page_text.strip():
        logger.warning(
            "Haiku classification: empty first-page text → safe-default complex_legal"
        )
        return ClassificationResult(
            doc_class="complex_legal",
            confidence=0.0,
            signal_used="haiku",
            page_count=page_count,
            file_size_bytes=file_size_bytes,
            avg_chars_per_page=avg_chars,
            reasoning="Empty first-page text; defaulting to complex_legal for safety",
        )

    prompt = _CLASSIFY_PROMPT.format(first_page_text=first_page_text)

    response = bedrock_client.converse(
        modelId=config.claude_haiku_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        toolConfig={
            "tools": [{"toolSpec": _CLASSIFY_TOOL}],
            "toolChoice": {"tool": {"name": "classify_document"}},
        },
        inferenceConfig={"maxTokens": config.haiku_max_tokens, "temperature": 0.0},
    )

    # Extract tool use input — guaranteed by toolChoice
    tool_input = None
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if block.get("toolUse", {}).get("name") == "classify_document":
            tool_input = block["toolUse"]["input"]
            break

    if not tool_input:
        logger.warning("Haiku returned no tool call → safe-default complex_legal")
        return ClassificationResult(
            doc_class="complex_legal",
            confidence=0.0,
            signal_used="haiku",
            page_count=page_count,
            file_size_bytes=file_size_bytes,
            avg_chars_per_page=avg_chars,
            reasoning="Haiku did not call classify_document; defaulting to complex_legal",
        )

    complexity = tool_input.get("complexity", "high")
    confidence = float(tool_input.get("confidence", 0.5))
    reasoning = tool_input.get("reasoning", "")

    # low confidence → safe-default to complex_legal regardless of complexity label
    if confidence < config.haiku_confidence_threshold:
        logger.info(
            "Haiku confidence %.2f < threshold %.2f → safe-default complex_legal",
            confidence,
            config.haiku_confidence_threshold,
        )
        doc_class = "complex_legal"
    else:
        doc_class = "complex_legal" if complexity == "high" else "simple"

    return ClassificationResult(
        doc_class=doc_class,
        confidence=confidence,
        signal_used="haiku",
        page_count=page_count,
        file_size_bytes=file_size_bytes,
        avg_chars_per_page=avg_chars,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify(
    bucket: str,
    key: str,
    config: PdfPipelineConfig,
    s3_client: Any | None = None,
    bedrock_client: Any | None = None,
) -> ClassificationResult:
    """Classify a PDF document using a 3-signal cascade.

    Signals are evaluated in order and short-circuit on the first confident
    decision. Signal 3 (Claude Haiku) is invoked only when Signals 1 and 2
    are inconclusive.

    Args:
        bucket: S3 bucket containing the PDF.
        key: S3 key of the PDF.
        config: Pipeline configuration.
        s3_client: Reusable boto3 S3 client (created if None).
        bedrock_client: Reusable boto3 bedrock-runtime client (created if None).

    Returns:
        ClassificationResult with doc_class, confidence, and signal provenance.
    """
    s3 = s3_client or boto3.client("s3", region_name=config.aws_region)
    bedrock = bedrock_client or boto3.client(
        "bedrock-runtime", region_name=config.aws_region
    )

    # --- Gather cheap metadata once for all signals ---
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        file_size_bytes = head["ContentLength"]
    except Exception as exc:
        raise RuntimeError(f"Cannot read S3 object {bucket}/{key}: {exc}") from exc

    page_count = _estimate_page_count(bucket, key, s3, config.xref_tail_bytes)
    # Extract page-1 text once (full download) and reuse for both Signal 1
    # (char density) and Signal 3 (Haiku prompt) — avoids a second download.
    first_page_text = _fetch_first_page_text(bucket, key, s3)
    avg_chars = float(len(first_page_text.strip()))

    logger.info(
        "Classifying %s/%s | size=%.2f MB | pages≈%d | avg_chars/page≈%.0f",
        bucket,
        key,
        file_size_bytes / (1024 * 1024),
        page_count,
        avg_chars,
    )

    # -----------------------------------------------------------------------
    # Signal 1: Free heuristics
    # -----------------------------------------------------------------------
    is_scanned = avg_chars < config.avg_chars_threshold and avg_chars > 0
    page_threshold_exceeded = page_count > config.scanned_page_threshold

    if is_scanned:
        logger.info(
            "Signal 1: avg_chars=%.0f < %d → SIMPLE (scanned)",
            avg_chars,
            config.avg_chars_threshold,
        )
        return ClassificationResult(
            doc_class="simple",
            confidence=0.95,
            signal_used="heuristic",
            page_count=page_count,
            file_size_bytes=file_size_bytes,
            avg_chars_per_page=avg_chars,
            reasoning=f"avg_chars_per_page={avg_chars:.0f} below threshold {config.avg_chars_threshold} → scanned document",
        )

    if page_threshold_exceeded and avg_chars < config.avg_chars_threshold * 3:
        logger.info(
            "Signal 1: page_count=%d > %d with low char density → SIMPLE",
            page_count,
            config.scanned_page_threshold,
        )
        return ClassificationResult(
            doc_class="simple",
            confidence=0.85,
            signal_used="heuristic",
            page_count=page_count,
            file_size_bytes=file_size_bytes,
            avg_chars_per_page=avg_chars,
            reasoning=f"page_count={page_count} > {config.scanned_page_threshold} with low character density",
        )

    # -----------------------------------------------------------------------
    # Signal 2: S3 object tag
    # -----------------------------------------------------------------------
    try:
        tag_resp = s3.get_object_tagging(Bucket=bucket, Key=key)
        tag_map = {t["Key"]: t["Value"] for t in tag_resp.get("TagSet", [])}
        doc_type_tag = tag_map.get(config.s3_tag_key, "").lower()

        if doc_type_tag in [v.lower() for v in config.complex_legal_tag_values]:
            logger.info(
                "Signal 2: tag '%s'='%s' → COMPLEX_LEGAL",
                config.s3_tag_key,
                doc_type_tag,
            )
            return ClassificationResult(
                doc_class="complex_legal",
                confidence=0.99,
                signal_used="s3_tag",
                page_count=page_count,
                file_size_bytes=file_size_bytes,
                avg_chars_per_page=avg_chars,
                reasoning=f"S3 tag {config.s3_tag_key}={doc_type_tag} maps to complex_legal",
            )

        if doc_type_tag in [v.lower() for v in config.simple_tag_values]:
            logger.info(
                "Signal 2: tag '%s'='%s' → SIMPLE", config.s3_tag_key, doc_type_tag
            )
            return ClassificationResult(
                doc_class="simple",
                confidence=0.99,
                signal_used="s3_tag",
                page_count=page_count,
                file_size_bytes=file_size_bytes,
                avg_chars_per_page=avg_chars,
                reasoning=f"S3 tag {config.s3_tag_key}={doc_type_tag} maps to simple",
            )

    except Exception:
        logger.debug("Could not read S3 tags for %s/%s", bucket, key, exc_info=True)

    # -----------------------------------------------------------------------
    # Signal 3: Claude Haiku (first page)
    # -----------------------------------------------------------------------
    logger.info("Signal 3: invoking Claude Haiku classifier")
    return _classify_with_haiku(
        # Truncate to ~3000 chars to keep the Haiku prompt cheap.
        first_page_text=first_page_text[:3000],
        page_count=page_count,
        file_size_bytes=file_size_bytes,
        avg_chars=avg_chars,
        config=config,
        bedrock_client=bedrock,
    )
