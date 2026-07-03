"""
pdf_section_scanner.py
======================
Scans a full PDF with pdfplumber to detect section header positions and build
a boundary map used by the mini-batch slicer in the orchestrator.

Invoked ONLY when a document is classified complex_legal AND exceeds the
single-call threshold (>550 pages or >18MB).

Heading detection strategy (priority order):
  1. Font-size heuristic — text blocks with font_size > (median + 2pt) that
     are bold or ALL-CAPS, spanning a single line.
  2. Numbering pattern regex — lines matching Part/Division/Section/Schedule
     numbering conventions common in mining and environmental legislation.
  3. All-caps short line — standalone uppercase lines ≤ 100 chars at the
     start of a paragraph.

Public API
----------
    scan_section_boundaries(pdf_bytes, config) -> SectionMap
    build_batches(section_map, pdf_bytes, config) -> list[BatchSlice]
"""
from __future__ import annotations

import io
import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF — for batch byte slicing
import pdfplumber

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heading detection patterns
# ---------------------------------------------------------------------------

# Covers: "Part 1", "Division 3A", "Section 12.4.1", "Schedule B",
#         "Chapter 5", "Appendix A", "Regulation 47"
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:part|division|section|schedule|chapter|appendix|regulation|clause|article)"
    r"[\s ]+[\dA-Z][\w.\-]*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SectionBoundary:
    title: str
    page_number: int   # 1-based
    detection_method: str  # "font_size" | "numbering" | "all_caps"


@dataclass
class SectionMap:
    boundaries: list[SectionBoundary]
    total_pages: int
    detection_summary: str


@dataclass
class BatchSlice:
    batch_index: int
    page_start: int    # 1-based, inclusive
    page_end: int      # 1-based, inclusive
    pdf_bytes: bytes
    size_mb: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_all_caps_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 100:
        return False
    alpha = [c for c in stripped if c.isalpha()]
    return len(alpha) >= 3 and all(c.isupper() for c in alpha)


def _extract_font_size_headings(pdf_path_or_stream: Any) -> list[tuple[int, str]]:
    """Return (page_number_1based, heading_text) for font-size-based headings."""
    results: list[tuple[int, str]] = []
    try:
        with pdfplumber.open(pdf_path_or_stream) as pdf:
            all_sizes: list[float] = []
            for page in pdf.pages:
                for word in page.extract_words(extra_attrs=["size"]):
                    sz = word.get("size")
                    if sz and sz > 0:
                        all_sizes.append(sz)

            if not all_sizes:
                return results

            median_size = statistics.median(all_sizes)
            heading_threshold = median_size + 2.0

            for page_num, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(extra_attrs=["size", "fontname"])
                if not words:
                    continue

                # Group words into lines by vertical proximity
                lines: list[list[dict]] = []
                current_line: list[dict] = []
                prev_top = None

                for word in sorted(words, key=lambda w: (w.get("top", 0), w.get("x0", 0))):
                    top = word.get("top", 0)
                    if prev_top is None or abs(top - prev_top) < 3:
                        current_line.append(word)
                    else:
                        if current_line:
                            lines.append(current_line)
                        current_line = [word]
                    prev_top = top
                if current_line:
                    lines.append(current_line)

                for line_words in lines:
                    sizes = [w.get("size", 0) for w in line_words if w.get("size")]
                    if not sizes:
                        continue
                    avg_line_size = sum(sizes) / len(sizes)
                    if avg_line_size < heading_threshold:
                        continue

                    line_text = " ".join(w.get("text", "") for w in line_words).strip()
                    if len(line_text) < 3 or len(line_text) > 200:
                        continue

                    font_names = [w.get("fontname", "").lower() for w in line_words]
                    is_bold = any("bold" in fn or "bd" in fn for fn in font_names)
                    is_caps = _is_all_caps_heading(line_text)

                    if is_bold or is_caps:
                        results.append((page_num, line_text))

    except Exception:
        logger.debug("Font-size heading scan failed", exc_info=True)

    return results


def _extract_numbered_headings(pdf_path_or_stream: Any) -> list[tuple[int, str]]:
    """Return (page_number_1based, heading_text) for regex-numbered headings."""
    results: list[tuple[int, str]] = []
    try:
        with pdfplumber.open(pdf_path_or_stream) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for line in text.splitlines():
                    stripped = line.strip()
                    if _NUMBERED_HEADING_RE.match(stripped) and len(stripped) <= 200:
                        results.append((page_num, stripped))
    except Exception:
        logger.debug("Numbered heading scan failed", exc_info=True)
    return results


def _deduplicate_boundaries(
    raw: list[tuple[int, str, str]]
) -> list[SectionBoundary]:
    """Deduplicate headings found by multiple detection methods on the same page."""
    seen: set[tuple[int, str]] = set()
    out: list[SectionBoundary] = []
    for page_num, title, method in sorted(raw, key=lambda x: (x[0], x[1])):
        key = (page_num, title[:60].lower())
        if key not in seen:
            seen.add(key)
            out.append(SectionBoundary(
                title=title,
                page_number=page_num,
                detection_method=method,
            ))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_section_boundaries(
    pdf_bytes: bytes,
    config: PdfPipelineConfig,
) -> SectionMap:
    """Scan the full PDF and detect section header positions.

    Uses pdfplumber for font-size and text analysis. Combines three detection
    strategies and deduplicates overlapping results.

    Args:
        pdf_bytes: Raw PDF bytes (full document).
        config: PdfPipelineConfig (currently unused but kept for future thresholds).

    Returns:
        SectionMap with a list of SectionBoundary entries sorted by page number.
    """
    pdf_stream = io.BytesIO(pdf_bytes)

    # Determine total page count via fitz (fast xref read)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()
    except Exception:
        total_pages = 0

    raw: list[tuple[int, str, str]] = []

    font_headings = _extract_font_size_headings(io.BytesIO(pdf_bytes))
    raw.extend((p, t, "font_size") for p, t in font_headings)

    numbered_headings = _extract_numbered_headings(io.BytesIO(pdf_bytes))
    raw.extend((p, t, "numbering") for p, t in numbered_headings)

    boundaries = _deduplicate_boundaries(raw)

    summary = (
        f"Found {len(boundaries)} section boundaries across {total_pages} pages "
        f"({len(font_headings)} font-size, {len(numbered_headings)} numbered)"
    )
    logger.info(summary)

    return SectionMap(
        boundaries=boundaries,
        total_pages=total_pages,
        detection_summary=summary,
    )


def build_batches(
    section_map: SectionMap,
    pdf_bytes: bytes,
    config: PdfPipelineConfig,
) -> list[BatchSlice]:
    """Slice the PDF into page batches that respect section boundaries.

    Each batch stays within config.batch_max_pages and config.batch_max_mb.
    Cuts are made at section boundaries — never inside a section.

    If the PDF has no detected section boundaries, batches are split by
    page count alone (acceptable fallback for edge-case documents).

    Args:
        section_map: Output of scan_section_boundaries().
        pdf_bytes: Full PDF bytes to slice.
        config: PdfPipelineConfig with batch size limits.

    Returns:
        Ordered list of BatchSlice objects (one per batch).
    """
    total_pages = section_map.total_pages
    if total_pages == 0:
        raise ValueError("Cannot build batches: total_pages is 0")

    # Build the set of valid cut points (section boundary pages)
    # If no boundaries detected, every page is a valid cut point (page-count split)
    if section_map.boundaries:
        boundary_pages = sorted({b.page_number for b in section_map.boundaries})
    else:
        logger.warning("No section boundaries detected; falling back to page-count splits")
        boundary_pages = list(range(1, total_pages + 1, config.batch_max_pages))

    # Open the full PDF for slicing
    src_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    batches: list[BatchSlice] = []
    batch_index = 0
    start_page = 1  # 1-based

    def _slice_to_bytes(start: int, end: int) -> bytes:
        """Extract pages [start, end] (1-based inclusive) into a new PDF bytes."""
        chunk = fitz.open()
        chunk.insert_pdf(src_doc, from_page=start - 1, to_page=end - 1)
        data = chunk.write()
        chunk.close()
        return data

    i = 0
    while start_page <= total_pages:
        # Tentatively accumulate pages until we hit a size or page limit
        candidate_end = start_page
        last_valid_boundary = start_page

        while candidate_end <= total_pages:
            # Check if adding one more page to this batch would exceed limits
            tentative_end = min(candidate_end + config.batch_max_pages - 1, total_pages)
            # Find the largest boundary page ≤ tentative_end
            valid_end = start_page
            for bp in boundary_pages:
                if start_page < bp <= tentative_end:
                    valid_end = bp - 1  # cut just before the next section starts
                elif bp == start_page:
                    valid_end = start_page

            # If we'd exceed batch_max_pages, cut at the last valid boundary
            pages_in_batch = tentative_end - start_page + 1
            if pages_in_batch > config.batch_max_pages:
                # Find the last boundary strictly within config.batch_max_pages
                cut_at = start_page + config.batch_max_pages - 1
                actual_end = start_page
                for bp in boundary_pages:
                    if start_page < bp <= cut_at:
                        actual_end = bp - 1
                if actual_end < start_page:
                    # No boundary found — force-cut at page limit
                    actual_end = cut_at
                batch_bytes = _slice_to_bytes(start_page, actual_end)
                size_mb = len(batch_bytes) / (1024 * 1024)

                batches.append(BatchSlice(
                    batch_index=batch_index,
                    page_start=start_page,
                    page_end=actual_end,
                    pdf_bytes=batch_bytes,
                    size_mb=size_mb,
                ))
                logger.info(
                    "Batch %d: pages %d–%d (%.2f MB)",
                    batch_index, start_page, actual_end, size_mb,
                )
                start_page = actual_end + 1
                batch_index += 1
                break
            else:
                # Try to take the full remaining document
                batch_bytes = _slice_to_bytes(start_page, total_pages)
                size_mb = len(batch_bytes) / (1024 * 1024)
                if size_mb <= config.batch_max_mb:
                    # The rest fits — create the final batch
                    batches.append(BatchSlice(
                        batch_index=batch_index,
                        page_start=start_page,
                        page_end=total_pages,
                        pdf_bytes=batch_bytes,
                        size_mb=size_mb,
                    ))
                    logger.info(
                        "Batch %d (final): pages %d–%d (%.2f MB)",
                        batch_index, start_page, total_pages, size_mb,
                    )
                    start_page = total_pages + 1
                    batch_index += 1
                    break
                else:
                    # Remaining too large — find a mid-point boundary cut
                    mid = start_page + (total_pages - start_page) // 2
                    cut_at = start_page
                    for bp in boundary_pages:
                        if start_page < bp <= mid:
                            cut_at = bp - 1
                    if cut_at <= start_page:
                        cut_at = start_page + config.batch_max_pages - 1
                    batch_bytes = _slice_to_bytes(start_page, cut_at)
                    size_mb = len(batch_bytes) / (1024 * 1024)
                    batches.append(BatchSlice(
                        batch_index=batch_index,
                        page_start=start_page,
                        page_end=cut_at,
                        pdf_bytes=batch_bytes,
                        size_mb=size_mb,
                    ))
                    logger.info(
                        "Batch %d: pages %d–%d (%.2f MB)",
                        batch_index, start_page, cut_at, size_mb,
                    )
                    start_page = cut_at + 1
                    batch_index += 1
                    break

    src_doc.close()
    logger.info("Built %d batches for %d total pages", len(batches), total_pages)
    return batches
