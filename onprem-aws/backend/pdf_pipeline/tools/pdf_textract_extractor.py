"""
pdf_textract_extractor.py
=========================
Simple path extractor for the PDF Vectorization Pipeline.

Uses Amazon Textract StartDocumentAnalysis (async) with LAYOUT + TABLES
feature types to extract structured text and tables from simple or scanned PDFs.

The document must already be in S3 — the S3 key is passed directly to Textract,
avoiding any in-memory download and bypassing Lambda memory limits on large
scanned PDFs.

Block reconstruction:
  Textract returns Block objects. This module traverses the block graph
  in geometric order, uses LAYOUT_SECTION_HEADER blocks to detect section
  boundaries, and groups LAYOUT_TEXT / TABLE blocks under their parent section.

Public API
----------
    extract_with_textract(bucket, key, file_size_bytes, total_pages,
                          config, textract_client, s3_client) -> TextractExtractionResult
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import boto3

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TextractExtractionResult:
    raw_sections: list[dict]   # list of dicts compatible with normalize_sections()
    job_id: str
    total_blocks: int
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

def _poll_textract_job(
    job_id: str,
    textract_client: Any,
    config: PdfPipelineConfig,
) -> list[dict]:
    """Poll GetDocumentAnalysis until SUCCEEDED; paginate NextToken.

    Returns the complete list of Block dicts from all pages.
    Raises RuntimeError on FAILED or timeout.
    """
    all_blocks: list[dict] = []
    next_token: str | None = None

    for attempt in range(config.textract_max_poll_attempts):
        params: dict[str, Any] = {"JobId": job_id}
        if next_token:
            params["NextToken"] = next_token

        resp = textract_client.get_document_analysis(**params)
        status = resp.get("JobStatus", "")

        if status == "FAILED":
            msg = resp.get("StatusMessage", "unknown reason")
            raise RuntimeError(f"Textract job {job_id} FAILED: {msg}")

        if status == "SUCCEEDED":
            all_blocks.extend(resp.get("Blocks", []))
            next_token = resp.get("NextToken")
            if not next_token:
                logger.info("Textract job %s SUCCEEDED | %d blocks total", job_id, len(all_blocks))
                return all_blocks
            # More pages — continue without sleeping
            continue

        # IN_PROGRESS — wait before next poll
        if attempt % 6 == 0:  # log every ~30s
            logger.info("Textract job %s IN_PROGRESS (attempt %d/%d)...", job_id, attempt + 1, config.textract_max_poll_attempts)
        time.sleep(config.textract_poll_interval_s)

    raise TimeoutError(
        f"Textract job {job_id} did not complete within "
        f"{config.textract_max_poll_attempts * config.textract_poll_interval_s:.0f}s"
    )


# ---------------------------------------------------------------------------
# Block reconstruction
# ---------------------------------------------------------------------------

_SECTION_HEADER_TYPES = {"LAYOUT_SECTION_HEADER"}
_CONTENT_TYPES = {
    "LAYOUT_TEXT", "LAYOUT_LIST", "LAYOUT_FIGURE",
    "LAYOUT_HEADER", "LAYOUT_FOOTER", "LAYOUT_PAGE_NUMBER",
    "LINE", "WORD",
}
_TABLE_TYPES = {"TABLE"}
_SKIP_TYPES = {"PAGE", "CELL", "WORD", "SELECTION_ELEMENT",
               "TABLE_TITLE", "TABLE_FOOTER", "MERGED_CELL",
               "QUERY", "QUERY_RESULT", "SIGNATURE", "KEY_VALUE_SET"}


def _get_block_text(block: dict, id_to_block: dict[str, dict]) -> str:
    """Recursively resolve the text of a block via its CHILD relationships."""
    block_type = block.get("BlockType", "")

    # Leaf text blocks carry text directly
    if block_type in ("WORD", "LINE"):
        return block.get("Text", "")

    # For container blocks, follow CHILD relationships
    child_ids: list[str] = []
    for rel in block.get("Relationships", []):
        if rel.get("Type") == "CHILD":
            child_ids.extend(rel.get("Ids", []))

    parts: list[str] = []
    for cid in child_ids:
        child = id_to_block.get(cid)
        if child:
            parts.append(_get_block_text(child, id_to_block))

    return " ".join(p for p in parts if p).strip()


def _extract_table_cells(
    table_block: dict,
    id_to_block: dict[str, dict],
) -> list[dict]:
    """Extract a list of cell dicts {row_index, col_index, text} from a TABLE block."""
    cells: list[dict] = []
    for rel in table_block.get("Relationships", []):
        if rel.get("Type") != "CHILD":
            continue
        for cell_id in rel.get("Ids", []):
            cell_block = id_to_block.get(cell_id)
            if not cell_block or cell_block.get("BlockType") not in ("CELL", "MERGED_CELL"):
                continue
            cell_text = _get_block_text(cell_block, id_to_block)
            cells.append({
                "row_index": cell_block.get("RowIndex", 0),
                "col_index": cell_block.get("ColumnIndex", 0),
                "text": cell_text,
            })
    return cells


def _sort_key(block: dict) -> tuple[int, float, float]:
    """Sort blocks by page → top → left for geometric reading order."""
    geo = block.get("Geometry", {}).get("BoundingBox", {})
    page = block.get("Page", 1)
    return (page, geo.get("Top", 0.0), geo.get("Left", 0.0))


def _reconstruct_sections(blocks: list[dict]) -> list[dict]:
    """Group blocks into sections using LAYOUT_SECTION_HEADER boundaries.

    Each LAYOUT_SECTION_HEADER opens a new section. Content blocks that follow
    are accumulated until the next header. TABLE blocks are extracted separately
    and stored in the section's tables list.

    Returns a list of raw section dicts compatible with normalize_sections().
    """
    id_to_block = {b["Id"]: b for b in blocks}

    # Sort all blocks in geometric reading order
    ordered = sorted(
        [b for b in blocks if b.get("BlockType") not in _SKIP_TYPES],
        key=_sort_key,
    )

    sections: list[dict] = []
    current_title = "untitled-0"
    current_body_parts: list[str] = []
    current_tables: list[dict] = []
    current_page_start: int = 1
    current_page_end: int = 1
    section_open = False

    def _flush_section() -> None:
        body = "\n".join(current_body_parts).strip()
        sections.append({
            "title": current_title,
            "body": body,
            "page_start": current_page_start,
            "page_end": current_page_end,
            "tables": list(current_tables),
            "citations": [],
        })

    for block in ordered:
        btype = block.get("BlockType", "")
        page = block.get("Page", 1)

        if btype in _SECTION_HEADER_TYPES:
            if section_open:
                _flush_section()
                current_body_parts.clear()
                current_tables.clear()
            current_title = _get_block_text(block, id_to_block) or "untitled"
            current_page_start = page
            current_page_end = page
            section_open = True

        elif btype in _TABLE_TYPES:
            if not section_open:
                section_open = True
            cells = _extract_table_cells(block, id_to_block)
            current_tables.append({"cells": cells})
            current_page_end = max(current_page_end, page)

        elif btype in _CONTENT_TYPES:
            if not section_open:
                section_open = True
            text = _get_block_text(block, id_to_block)
            # Skip page numbers and short noise
            if text and len(text.strip()) > 2:
                current_body_parts.append(text)
            current_page_end = max(current_page_end, page)

    # Flush last section
    if section_open or current_body_parts or current_tables:
        _flush_section()

    return sections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_with_textract(
    bucket: str,
    key: str,
    file_size_bytes: int,
    total_pages: int,
    config: PdfPipelineConfig,
    textract_client: Any | None = None,
    s3_client: Any | None = None,
) -> TextractExtractionResult:
    """Extract structured sections from a simple/scanned PDF using Textract.

    Starts an async Textract StartDocumentAnalysis job (LAYOUT + TABLES),
    polls until completion, reconstructs section boundaries from the Block graph,
    and returns raw section dicts ready for pdf_normalizer.normalize_sections().

    The PDF is read directly from S3 by Textract — no in-memory download needed.

    Args:
        bucket: S3 bucket containing the PDF.
        key: S3 key of the PDF.
        file_size_bytes: File size in bytes (for logging).
        total_pages: Estimated page count (for logging).
        config: PdfPipelineConfig with Textract settings.
        textract_client: Reusable boto3 textract client (created if None).
        s3_client: Unused here (kept for API consistency).

    Returns:
        TextractExtractionResult with raw_sections and job provenance.
    """
    textract = textract_client or boto3.client("textract", region_name=config.aws_region)

    logger.info(
        "Starting Textract job | s3://%s/%s | size=%.2f MB | pages≈%d",
        bucket, key, file_size_bytes / (1024 * 1024), total_pages,
    )

    errors: list[str] = []

    try:
        start_resp = textract.start_document_analysis(
            DocumentLocation={
                "S3Object": {"Bucket": bucket, "Name": key}
            },
            FeatureTypes=config.textract_feature_types,
        )
        job_id = start_resp["JobId"]
        logger.info("Textract job started: %s", job_id)
    except Exception as exc:
        raise RuntimeError(f"Failed to start Textract job for {bucket}/{key}: {exc}") from exc

    try:
        blocks = _poll_textract_job(job_id, textract, config)
    except (RuntimeError, TimeoutError) as exc:
        errors.append(str(exc))
        return TextractExtractionResult(
            raw_sections=[],
            job_id=job_id,
            total_blocks=0,
            errors=errors,
        )

    raw_sections = _reconstruct_sections(blocks)
    logger.info(
        "Textract reconstruction complete: %d blocks → %d sections",
        len(blocks), len(raw_sections),
    )

    return TextractExtractionResult(
        raw_sections=raw_sections,
        job_id=job_id,
        total_blocks=len(blocks),
        errors=errors,
    )
