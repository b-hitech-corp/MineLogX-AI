"""
pdf_normalizer.py
=================
Unified section schema for the PDF Vectorization Pipeline.

Defines the SectionRecord and SectionMetadata dataclasses that form the
contract between extraction (Textract / Claude) and embedding (Titan Embed v2).

Also provides normalize_sections() which cleans raw extraction output from
either path into a consistent list of SectionRecord objects ready for embedding.

Normalization operations applied to every section:
  1. Title cleaning — strip whitespace, collapse internals, truncate to 200 chars.
  2. Body cleaning — NFC Unicode normalization, deduplicate newlines, strip
     page-number boilerplate lines (e.g. lone digit lines).
  3. Body length guard — sections exceeding titan_max_input_chars are split into
     sub-sections using character-overlap chunking (reused from pdf_vectorizer_EC2).
  4. Empty section filter — sections with fewer than min_section_body_chars
     characters are discarded.
  5. Table serialization — Textract table dicts are converted to pipe-delimited
     markdown and appended to the body for BM25 searchability.
  6. section_id generation — "{sanitized_filename}-s{index:04d}"

Public API
----------
    normalize_sections(raw_sections, extraction_method, metadata, config) -> list[SectionRecord]
    build_section_metadata(bucket, key, doc_class, file_size_bytes, total_pages) -> SectionMetadata
"""
from __future__ import annotations

import re
import unicodedata
import logging
from dataclasses import dataclass, field
from typing import Any

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SectionMetadata:
    source_bucket: str
    source_key: str
    doc_class: str            # "complex_legal" | "simple"
    file_size_bytes: int
    total_pages: int
    schema_version: str = SCHEMA_VERSION


@dataclass
class SectionRecord:
    section_id: str           # "{sanitized_filename}-s{index:04d}"
    title: str                # section heading; "untitled-{n}" if absent
    body: str                 # full section text (normalized, embedding-ready)
    page_start: int           # 1-based
    page_end: int             # 1-based, inclusive
    extraction_method: str    # "textract" | "claude_native" | "claude_batch"
    batch_index: int          # 0 for single-call; batch number for mini-batch
    tables: list[dict]        # Textract table cells; empty for Claude paths
    citations: list[dict]     # Bedrock Citations API data; empty for Textract
    metadata: SectionMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _clean_title(title: str, max_chars: int) -> str:
    title = unicodedata.normalize("NFC", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:max_chars]


def _clean_body(body: str) -> str:
    body = unicodedata.normalize("NFC", body)
    # Remove lone page-number lines
    body = _PAGE_NUMBER_RE.sub("", body)
    # Collapse excessive newlines
    body = _MULTI_NEWLINE_RE.sub("\n\n", body)
    # Collapse internal spaces/tabs
    body = _MULTI_SPACE_RE.sub(" ", body)
    return body.strip()


def _tables_to_markdown(tables: list[dict]) -> str:
    """Serialize Textract table cell structures to pipe-delimited markdown."""
    if not tables:
        return ""
    lines: list[str] = []
    for table in tables:
        rows: dict[int, dict[int, str]] = {}
        for cell in table.get("cells", []):
            row_idx = cell.get("row_index", 0)
            col_idx = cell.get("col_index", 0)
            text = cell.get("text", "").strip()
            rows.setdefault(row_idx, {})[col_idx] = text
        for row_idx in sorted(rows):
            row = rows[row_idx]
            line = " | ".join(row.get(c, "") for c in sorted(row))
            lines.append(f"| {line} |")
        lines.append("")  # blank line between tables
    return "\n".join(lines)


def _chunk_by_length(
    text: str,
    max_chars: int = 8_000,
    overlap: int = 200,
) -> list[str]:
    """Split text into fixed-size chunks with overlap (reused logic from pdf_vectorizer_EC2)."""
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap
    chunks = [text[i: i + max_chars] for i in range(0, len(text), step)]
    if len(chunks) > 1 and len(chunks[-1]) <= overlap:
        chunks = chunks[:-1]
    return chunks


def _sanitize_filename(filename: str) -> str:
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = name.lower()
    name = re.sub(r"[^a-z0-9\-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_section_metadata(
    bucket: str,
    key: str,
    doc_class: str,
    file_size_bytes: int,
    total_pages: int,
) -> SectionMetadata:
    """Construct a SectionMetadata instance from pipeline inputs."""
    return SectionMetadata(
        source_bucket=bucket,
        source_key=key,
        doc_class=doc_class,
        file_size_bytes=file_size_bytes,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# Main normalizer
# ---------------------------------------------------------------------------

def normalize_sections(
    raw_sections: list[dict[str, Any]],
    extraction_method: str,
    metadata: SectionMetadata,
    config: PdfPipelineConfig,
    batch_index: int = 0,
) -> list[SectionRecord]:
    """Normalize raw extraction output into a consistent list of SectionRecord.

    Accepts raw section dicts from either the Textract extractor or the Claude
    extractor. Both must supply: title, body, page_start, page_end.
    The Textract extractor additionally supplies: tables (list of table dicts).
    The Claude extractor additionally supplies: citations (list of citation dicts).

    Args:
        raw_sections: List of dicts from an extraction module.
        extraction_method: "textract" | "claude_native" | "claude_batch"
        metadata: SectionMetadata with source file provenance.
        config: PdfPipelineConfig with thresholds.
        batch_index: Mini-batch index (0 for single-call and Textract paths).

    Returns:
        List of clean SectionRecord objects ready for Titan embedding.
    """
    filename = metadata.source_key.split("/")[-1]
    base_name = _sanitize_filename(filename)

    records: list[SectionRecord] = []
    section_counter = 0

    for raw in raw_sections:
        raw_title = raw.get("title", "") or ""
        raw_body = raw.get("body", "") or ""
        page_start = int(raw.get("page_start", 1))
        page_end = int(raw.get("page_end", page_start))
        tables = raw.get("tables", [])
        citations = raw.get("citations", [])

        # Clean title
        title = _clean_title(raw_title, config.max_title_chars) or f"untitled-{section_counter}"

        # Clean body
        body = _clean_body(raw_body)

        # Append serialized tables to body (Textract path)
        if tables:
            table_md = _tables_to_markdown(tables)
            if table_md.strip():
                body = body + "\n\n" + table_md

        # Discard empty sections
        if len(body) < config.min_section_body_chars:
            logger.debug("Discarding short section '%s' (%d chars)", title, len(body))
            continue

        # Body length guard: split oversized sections into sub-sections
        if len(body) > config.titan_max_input_chars:
            sub_chunks = _chunk_by_length(
                body,
                max_chars=config.titan_max_input_chars,
                overlap=200,
            )
            total_sub = len(sub_chunks)
            logger.debug(
                "Section '%s' (%d chars) split into %d sub-sections",
                title, len(body), total_sub,
            )
            for sub_idx, sub_body in enumerate(sub_chunks):
                sub_title = f"{title} (part {sub_idx + 1} of {total_sub})"
                section_id = f"{base_name}-s{section_counter:04d}"
                records.append(SectionRecord(
                    section_id=section_id,
                    title=sub_title,
                    body=sub_body,
                    page_start=page_start,
                    page_end=page_end,
                    extraction_method=extraction_method,
                    batch_index=batch_index,
                    tables=tables if sub_idx == 0 else [],
                    citations=citations if sub_idx == 0 else [],
                    metadata=metadata,
                ))
                section_counter += 1
        else:
            section_id = f"{base_name}-s{section_counter:04d}"
            records.append(SectionRecord(
                section_id=section_id,
                title=title,
                body=body,
                page_start=page_start,
                page_end=page_end,
                extraction_method=extraction_method,
                batch_index=batch_index,
                tables=tables,
                citations=citations,
                metadata=metadata,
            ))
            section_counter += 1

    logger.info(
        "Normalized %d raw sections → %d SectionRecords (method=%s, batch=%d)",
        len(raw_sections), len(records), extraction_method, batch_index,
    )
    return records
