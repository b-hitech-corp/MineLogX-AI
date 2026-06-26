"""
pdf_claude_extractor.py
=======================
Complex-path extractor for the PDF Vectorization Pipeline.

Uses Amazon Bedrock Claude Sonnet 4's native PDF input via the Converse API,
with Citations API enabled, to extract semantically structured sections from
complex legal and regulatory PDFs.

Handles two sub-paths:
  - Single call  (≤550 pages AND ≤18MB): Full PDF sent in one Converse call.
  - Mini-batch   (>550 pages OR >18MB):  Each BatchSlice from pdf_section_scanner
                 is sent sequentially. Batches N>0 receive carry-over context
                 (title + summary of the last section from the previous batch)
                 to maintain continuity across page-slice boundaries.

API details (confirmed from AWS documentation, June 2025):
  - Document content block: format='pdf', source.bytes=<raw PDF bytes>
  - Citations: citations={'enabled': True} on the document block
  - Available on: Claude Sonnet 4, Claude Opus 4, Claude Sonnet 3.7, Claude 3.5v2
  - Response: content array of CitationsContentBlock or plain TextBlock

Public API
----------
    extract_with_claude(pdf_bytes, bucket, key, file_size_bytes, total_pages,
                        config, bedrock_client, **opts) -> ClaudeExtractionResult
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import boto3

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ClaudeExtractionResult:
    raw_sections: list[dict]   # compatible with normalize_sections()
    input_tokens: int
    output_tokens: int
    batch_index: int
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_BASE_EXTRACTION_PROMPT = """\
You are a legal document analyst specializing in mining, environmental, and safety regulatory documents.

Extract every distinct section from this regulatory PDF as a structured JSON array.

For each section, produce an object with exactly these fields:
  "title"      : The exact section heading as it appears in the document (string).
  "body"       : The complete verbatim text of the section, preserving all sub-clauses,
                 numbered lists, tables (as pipe-delimited text), and schedules (string).
  "page_start" : The page number where this section begins, 1-based (integer).
  "page_end"   : The page number where this section ends, inclusive, 1-based (integer).

Rules:
  - Do NOT summarize. Return the complete verbatim text of every section.
  - Do NOT merge adjacent sections. Each heading in the document = one entry.
  - Sub-sections must be included within their parent section's body, not as separate entries.
  - Tables: represent as pipe-delimited rows within the body text.
  - If a page number cannot be determined, use your best estimate.

Output ONLY the JSON array. No explanation, no markdown fencing, no preamble.\
"""

_CARRY_OVER_TEMPLATE = """\
[Context from previous batch]
Last section processed: "{last_title}"
Brief summary of that section: {last_summary}

Continue extracting sections from where the previous batch ended.
Do not repeat content already extracted. Start from the next section.

"""


def _build_extraction_prompt(context_note: str = "") -> str:
    if context_note:
        return context_note + _BASE_EXTRACTION_PROMPT
    return _BASE_EXTRACTION_PROMPT


def _sanitize_doc_name(key: str) -> str:
    """Produce a Bedrock-safe document name (alphanumeric + hyphens, max 200 chars)."""
    filename = key.split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9\s\-]", "-", filename)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:200] or "document"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_claude_sections(raw_text: str) -> list[dict]:
    """Parse Claude's JSON array response into a list of section dicts.

    Tries strict json.loads first. Falls back to extracting the JSON array
    from anywhere in the response text (handles markdown fencing or preambles).
    """
    text = raw_text.strip()

    # Attempt 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract outermost [...] block
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Attempt 3: try json-repair if available
    try:
        from json_repair import repair_json  # optional dependency
        repaired = repair_json(text)
        result = json.loads(repaired)
        if isinstance(result, list):
            logger.warning("Used json-repair to parse Claude response")
            return result
    except Exception:
        pass

    logger.error("Could not parse Claude response as JSON array. Raw preview: %s", text[:500])
    return []


def _extract_citations(content_block: dict) -> list[dict]:
    """Extract citation metadata from a Bedrock CitationsContentBlock."""
    citations: list[dict] = []
    for citation in content_block.get("citations", []):
        loc = citation.get("location", {})
        citations.append({
            "page": loc.get("pageNumber"),
            "source_content": citation.get("sourceContent", ""),
            "title": citation.get("title", ""),
        })
    return citations


def _extract_response_text_and_citations(
    response: dict,
) -> tuple[str, list[dict]]:
    """Parse the Converse API response into (text, citations).

    Handles both plain TextBlock and CitationsContentBlock responses.
    """
    text_parts: list[str] = []
    all_citations: list[dict] = []

    output_message = response.get("output", {}).get("message", {})
    for block in output_message.get("content", []):
        # Plain text block
        if "text" in block:
            text_parts.append(block["text"])

        # Citations content block
        elif "citationsContent" in block:
            for sub_block in block["citationsContent"].get("content", []):
                if "text" in sub_block:
                    text_parts.append(sub_block["text"])
            all_citations.extend(_extract_citations(block.get("citationsContent", {})))

    return "\n".join(text_parts), all_citations


# ---------------------------------------------------------------------------
# Core extraction call
# ---------------------------------------------------------------------------

def _call_claude(
    pdf_bytes: bytes,
    doc_name: str,
    context_note: str,
    config: PdfPipelineConfig,
    bedrock_client: Any,
) -> tuple[list[dict], int, int]:
    """Single Bedrock Converse call. Returns (raw_sections, input_tokens, output_tokens)."""
    prompt = _build_extraction_prompt(context_note)

    content_blocks: list[dict] = [
        {
            "document": {
                "format": "pdf",
                "name": doc_name,
                "source": {"bytes": pdf_bytes},
                "citations": {"enabled": config.citations_enabled},
            }
        },
        {"text": prompt},
    ]

    response = bedrock_client.converse(
        modelId=config.claude_model_id,
        messages=[{"role": "user", "content": content_blocks}],
        inferenceConfig={
            "maxTokens": config.claude_max_tokens,
            "temperature": 0.0,
        },
    )

    input_tokens = response.get("usage", {}).get("inputTokens", 0)
    output_tokens = response.get("usage", {}).get("outputTokens", 0)

    response_text, citations = _extract_response_text_and_citations(response)
    raw_sections = _parse_claude_sections(response_text)

    # Attach top-level citations to the last section if parsing returned sections
    # but the Citations API returned aggregated citations at the response level
    if citations and raw_sections:
        raw_sections[-1].setdefault("citations", []).extend(citations)

    logger.info(
        "Claude call complete | %d sections | %d input tokens | %d output tokens",
        len(raw_sections), input_tokens, output_tokens,
    )
    return raw_sections, input_tokens, output_tokens


# ---------------------------------------------------------------------------
# Carry-over context builder
# ---------------------------------------------------------------------------

def _build_carry_over(last_section: dict) -> str:
    """Build the context carry-over string for the next mini-batch prompt."""
    title = last_section.get("title", "Unknown section")
    body = last_section.get("body", "")
    # Summarize: first 300 chars of the body
    summary_preview = body[:300].replace("\n", " ").strip()
    if len(body) > 300:
        summary_preview += "..."
    return _CARRY_OVER_TEMPLATE.format(
        last_title=title,
        last_summary=summary_preview,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_with_claude(
    pdf_bytes: bytes,
    bucket: str,
    key: str,
    file_size_bytes: int,
    total_pages: int,
    config: PdfPipelineConfig,
    bedrock_client: Any | None = None,
    page_start_offset: int = 1,
    batch_index: int = 0,
    context_note: str = "",
) -> ClaudeExtractionResult:
    """Extract semantically structured sections using Claude Sonnet 4 native PDF.

    This is the single-call path. For the mini-batch path, the orchestrator
    calls this function once per BatchSlice, passing the appropriate batch_index
    and context_note (carry-over from the previous batch).

    Args:
        pdf_bytes: Raw PDF bytes for this call (full doc or one batch slice).
        bucket: Source S3 bucket (for logging and metadata).
        key: Source S3 key (for logging and metadata).
        file_size_bytes: Size of the original full document in bytes.
        total_pages: Total pages in the original full document.
        config: PdfPipelineConfig.
        bedrock_client: Reusable boto3 bedrock-runtime client.
        page_start_offset: The 1-based page number of the first page in pdf_bytes
            within the original document. Used to adjust page numbers in output.
        batch_index: Mini-batch index (0 for single-call path).
        context_note: Carry-over context from the previous batch (empty for batch 0).

    Returns:
        ClaudeExtractionResult with raw_sections and token usage.
    """
    bedrock = bedrock_client or boto3.client("bedrock-runtime", region_name=config.aws_region)
    doc_name = _sanitize_doc_name(key)
    size_mb = len(pdf_bytes) / (1024 * 1024)

    logger.info(
        "Claude extraction | batch=%d | %.2f MB | pages≈%d | offset_page=%d",
        batch_index, size_mb, total_pages, page_start_offset,
    )

    errors: list[str] = []
    raw_sections: list[dict] = []
    input_tokens = 0
    output_tokens = 0

    try:
        raw_sections, input_tokens, output_tokens = _call_claude(
            pdf_bytes=pdf_bytes,
            doc_name=doc_name,
            context_note=context_note,
            config=config,
            bedrock_client=bedrock,
        )
    except Exception as exc:
        error_msg = f"Claude call failed for batch {batch_index}: {exc}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return ClaudeExtractionResult(
            raw_sections=[],
            input_tokens=0,
            output_tokens=0,
            batch_index=batch_index,
            errors=errors,
        )

    # Adjust page numbers by the batch offset (page 1 in this slice = page_start_offset in doc)
    if page_start_offset > 1:
        for section in raw_sections:
            section["page_start"] = section.get("page_start", 1) + page_start_offset - 1
            section["page_end"] = section.get("page_end", 1) + page_start_offset - 1

    # Ensure citations key exists on every section
    for section in raw_sections:
        section.setdefault("citations", [])
        section.setdefault("tables", [])

    return ClaudeExtractionResult(
        raw_sections=raw_sections,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        batch_index=batch_index,
        errors=errors,
    )


def build_carry_over_context(last_section: dict) -> str:
    """Build the carry-over context string to inject at the start of the next batch prompt.

    Call this after each batch completes (except the last) to maintain
    section continuity across mini-batch boundaries.

    Args:
        last_section: The last raw section dict from the completed batch.

    Returns:
        Formatted context string to pass as context_note to the next batch's
        extract_with_claude() call.
    """
    return _build_carry_over(last_section)
