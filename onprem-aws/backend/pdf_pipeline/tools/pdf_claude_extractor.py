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
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.prompts import BASE_EXTRACTION_PROMPT, CARRY_OVER_TEMPLATE
from pdf_pipeline.tools.tool_schemas import EXTRACT_TOOL

logger = logging.getLogger(__name__)


class MaxTokensTruncationError(Exception):
    """Claude's tool response was truncated at the max_tokens ceiling.

    Not retried — an identical request would truncate again the same way.
    """


class PageLimitExceededError(Exception):
    """A Converse call was sent more than Bedrock's 100-page-per-PDF hard limit.

    Not retried — the same oversized payload would be rejected again. With the
    routing thresholds pinned at/below the limit (claude_max_pages /
    batch_max_pages), this should be unreachable in normal operation; if it
    fires, batching or page-count estimation produced a batch that is too large.
    """


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ClaudeExtractionResult:
    raw_sections: list[dict]  # compatible with normalize_sections()
    input_tokens: int
    output_tokens: int
    batch_index: int
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
# Prompt text lives in prompts.py (BASE_EXTRACTION_PROMPT, CARRY_OVER_TEMPLATE);
# the emit_sections tool schema lives in tool_schemas.py (EXTRACT_TOOL).


def _build_extraction_prompt(context_note: str = "") -> str:
    if context_note:
        return context_note + BASE_EXTRACTION_PROMPT
    return BASE_EXTRACTION_PROMPT


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

    logger.error(
        "Could not parse Claude response as JSON array. Raw preview: %s", text[:500]
    )
    return []


def _extract_citations(content_block: dict) -> list[dict]:
    """Extract citation metadata from a Bedrock CitationsContentBlock."""
    citations: list[dict] = []
    for citation in content_block.get("citations", []):
        loc = citation.get("location", {})
        citations.append(
            {
                "page": loc.get("pageNumber"),
                "source_content": citation.get("sourceContent", ""),
                "title": citation.get("title", ""),
            }
        )
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


def _effective_read_timeout(
    config: PdfPipelineConfig, deadline_ts: float | None
) -> float | None:
    """Bound the Bedrock read timeout by the Lambda invocation deadline.

    Returns None when there is no deadline to respect (the static
    config.bedrock_read_timeout_s applies unchanged in that case).
    """
    if deadline_ts is None:
        return None
    remaining = deadline_ts - time.time()
    return max(
        1.0,
        min(config.bedrock_read_timeout_s, remaining - config.bedrock_deadline_safety_margin_s),
    )


def _call_claude(
    pdf_bytes: bytes,
    doc_name: str,
    context_note: str,
    config: PdfPipelineConfig,
    bedrock_client: Any,
    deadline_ts: float | None = None,
) -> tuple[list[dict], int, int]:
    """Single Bedrock Converse call using forced tool use.

    Returns (raw_sections, input_tokens, output_tokens). The emit_sections tool is forced
    via toolChoice, so Claude returns the sections as parsed JSON in toolUse.input rather
    than as a free-text JSON string — robust against quotes/brackets in legal text.
    (Citations are not used on this path: forcing a tool suppresses text generation, and
    citations are a property of generated text, so the two cannot combine. Page-level
    provenance is retained via each section's page_start/page_end.)

    If deadline_ts is set and less time remains than bedrock_read_timeout_s + the
    configured safety margin, this call is issued on a fresh client whose read
    timeout is tightened to what's actually left — so a call that can't finish
    in time fails cleanly instead of running until the Lambda is hard-killed.
    """
    prompt = _build_extraction_prompt(context_note)

    content_blocks: list[dict] = [
        {
            "document": {
                "format": "pdf",
                "name": doc_name,
                "source": {"bytes": pdf_bytes},
            }
        },
        {"text": prompt},
    ]

    call_client = bedrock_client
    effective_timeout = _effective_read_timeout(config, deadline_ts)
    if effective_timeout is not None and effective_timeout < config.bedrock_read_timeout_s:
        logger.info(
            "Deadline-bounded Bedrock read timeout for this call: %.1fs", effective_timeout
        )
        call_client = boto3.client(
            "bedrock-runtime",
            region_name=config.aws_region,
            config=BotoConfig(
                connect_timeout=config.bedrock_connect_timeout_s,
                read_timeout=effective_timeout,
                retries={"max_attempts": 1},
                max_pool_connections=config.bedrock_max_pool_connections,
            ),
        )

    response = call_client.converse(
        modelId=config.claude_model_id,
        messages=[{"role": "user", "content": content_blocks}],
        toolConfig={
            "tools": [EXTRACT_TOOL],
            "toolChoice": {"tool": {"name": "emit_sections"}},
        },
        inferenceConfig={
            "maxTokens": config.claude_max_tokens,
            "temperature": 0.0,
        },
    )

    input_tokens = response.get("usage", {}).get("inputTokens", 0)
    output_tokens = response.get("usage", {}).get("outputTokens", 0)

    raw_sections = _extract_tool_sections(response)

    logger.info(
        "Claude call complete | %d sections | %d input tokens | %d output tokens",
        len(raw_sections),
        input_tokens,
        output_tokens,
    )
    return raw_sections, input_tokens, output_tokens


def _classify_error(exc: Exception, config: PdfPipelineConfig) -> str:
    """Classify an exception from a Claude call for the retry wrapper.

    Returns "truncation" (max_tokens truncation — raise MaxTokensTruncationError,
    never retry), "page_limit" (>100 PDF pages — raise PageLimitExceededError,
    never retry), "retryable" (transient — backoff and retry), or "fatal"
    (anything else — propagate on the first attempt).
    """
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code", "")
        message = error.get("Message", "").lower()
        if code == "ValidationException":
            if any(
                marker in message for marker in config.claude_truncation_error_markers
            ):
                return "truncation"
            if any(
                marker in message for marker in config.claude_page_limit_error_markers
            ):
                return "page_limit"
        if code in config.claude_retryable_error_codes:
            return "retryable"
        return "fatal"
    if isinstance(exc, (ReadTimeoutError, ConnectTimeoutError, EndpointConnectionError)):
        return "retryable"
    return "fatal"


def _call_claude_with_retry(
    pdf_bytes: bytes,
    doc_name: str,
    context_note: str,
    config: PdfPipelineConfig,
    bedrock_client: Any,
    deadline_ts: float | None = None,
) -> tuple[list[dict], int, int]:
    """Wrap _call_claude with typed retry/backoff (mirrors pdf_titan_embedder.py).

    Retryable errors (throttling, transient service errors, connection/read
    timeouts) get exponential backoff with jitter, up to
    config.claude_call_max_retries attempts. A max_tokens truncation raises
    MaxTokensTruncationError immediately — retrying an identical request would
    truncate again. Any other error propagates on the first attempt.
    """
    last_exc: Exception | None = None
    for attempt in range(config.claude_call_max_retries):
        try:
            return _call_claude(
                pdf_bytes=pdf_bytes,
                doc_name=doc_name,
                context_note=context_note,
                config=config,
                bedrock_client=bedrock_client,
                deadline_ts=deadline_ts,
            )
        except Exception as exc:
            kind = _classify_error(exc, config)
            if kind == "truncation":
                raise MaxTokensTruncationError(str(exc)) from exc
            if kind == "page_limit":
                raise PageLimitExceededError(
                    "Bedrock rejected a PDF over its 100-page limit despite "
                    f"claude_max_pages={config.claude_max_pages}/"
                    f"batch_max_pages={config.batch_max_pages}; batching or "
                    f"page-count estimation is producing oversized batches: {exc}"
                ) from exc
            if kind != "retryable":
                raise
            wait = config.claude_call_retry_base_s * (2**attempt) + random.uniform(
                0, config.claude_call_retry_base_s
            )
            logger.warning(
                "Claude call retryable error (attempt %d/%d, type=%s): %s — retrying in %.1fs",
                attempt + 1,
                config.claude_call_max_retries,
                type(exc).__name__,
                exc,
                wait,
            )
            time.sleep(wait)
            last_exc = exc

    raise RuntimeError(
        f"Claude call failed after {config.claude_call_max_retries} retries"
    ) from last_exc


def _extract_tool_sections(response: dict) -> list[dict]:
    """Pull the sections list from the forced emit_sections tool call.

    Bedrock returns the array as already-parsed JSON in toolUse.input — no markdown fences
    or escaping to recover from. Falls back to the legacy text parser only if (unexpectedly)
    no tool block is present.
    """
    message = response.get("output", {}).get("message", {})
    for block in message.get("content", []):
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == "emit_sections":
            inp = tool_use.get("input", {})
            sections = inp.get("sections", []) if isinstance(inp, dict) else []
            # Happy path: the model returned a native array.
            if isinstance(sections, list):
                return sections
            # Known Claude quirk: large/complex array values come back as a JSON
            # *string* — and that string can carry unescaped quotes from the source
            # text (e.g. '("Act")'), so parse it tolerantly (json -> regex -> repair).
            if isinstance(sections, str):
                logger.warning(
                    "emit_sections returned a stringified array (%d chars); parsing tolerantly",
                    len(sections),
                )
                return _parse_claude_sections(sections)
            return []

    # Defensive fallback: if no tool block came back, try parsing any text content.
    text, _ = _extract_response_text_and_citations(response)
    if text.strip():
        logger.warning(
            "No emit_sections tool block in response; falling back to text JSON parse"
        )
        return _parse_claude_sections(text)

    logger.error(
        "Claude returned neither an emit_sections tool call nor parseable text"
    )
    return []


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
    return CARRY_OVER_TEMPLATE.format(
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
    deadline_ts: float | None = None,
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
        deadline_ts: Optional Lambda invocation deadline (time.time()-based) used
            to bound this call's effective read timeout. None disables bounding.

    Returns:
        ClaudeExtractionResult with raw_sections and token usage.

    Raises:
        MaxTokensTruncationError: propagates unhandled — the caller must decide
            how to handle a truncated batch (e.g. skip vs. abort vs. re-split).
    """
    bedrock = bedrock_client or boto3.client(
        "bedrock-runtime", region_name=config.aws_region
    )
    doc_name = _sanitize_doc_name(key)
    size_mb = len(pdf_bytes) / (1024 * 1024)

    logger.info(
        "Claude extraction | batch=%d | %.2f MB | pages≈%d | offset_page=%d",
        batch_index,
        size_mb,
        total_pages,
        page_start_offset,
    )

    errors: list[str] = []
    raw_sections: list[dict] = []
    input_tokens = 0
    output_tokens = 0

    try:
        raw_sections, input_tokens, output_tokens = _call_claude_with_retry(
            pdf_bytes=pdf_bytes,
            doc_name=doc_name,
            context_note=context_note,
            config=config,
            bedrock_client=bedrock,
            deadline_ts=deadline_ts,
        )
    except (MaxTokensTruncationError, PageLimitExceededError):
        raise
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
