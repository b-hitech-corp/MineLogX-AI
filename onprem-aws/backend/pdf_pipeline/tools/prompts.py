"""
Prompts for the PDF Vectorization Pipeline.

Prose prompts kept separate from structured tool schemas (see tool_schemas.py)
so each file stays single-purpose. Keeping prompts in one place makes them easy
to review, version, and test.

Consumed by:
  - tools/pdf_claude_extractor.py — BASE_EXTRACTION_PROMPT, CARRY_OVER_TEMPLATE
  - tools/pdf_classifier.py        — CLASSIFY_PROMPT
"""

# ---------------------------------------------------------------------------
# Complex-path extraction (Claude Sonnet 4, native PDF via Converse)
# ---------------------------------------------------------------------------

BASE_EXTRACTION_PROMPT = """\
You are a legal document analyst specializing in mining, environmental, and safety regulatory documents.

Read the attached regulatory PDF and extract EVERY distinct section, then return them by
calling the emit_sections tool. For each section provide:
  title      : the exact section heading as it appears in the document.
  body       : the complete VERBATIM text of the section, preserving sub-clauses, numbered
               lists, tables (as pipe-delimited rows), and schedules.
  page_start : 1-based page where the section begins.
  page_end   : 1-based inclusive page where the section ends.

Rules:
  - Do NOT summarize; return the complete verbatim text of every section.
  - Do NOT merge adjacent sections — each heading is its own entry.
  - Sub-sections belong inside their parent section's body, not as separate entries.
  - If a page number cannot be determined, use your best estimate.

Call emit_sections exactly once, with every section in document order.\
"""

# Injected before BASE_EXTRACTION_PROMPT for mini-batch continuation (batches N>0),
# to maintain continuity across page-slice boundaries.
CARRY_OVER_TEMPLATE = """\
[Context from previous batch]
Last section processed: "{last_title}"
Brief summary of that section: {last_summary}

Continue extracting sections from where the previous batch ended.
Do not repeat content already extracted. Start from the next section.

"""


# ---------------------------------------------------------------------------
# Document classifier (Claude Haiku, first-page complexity signal)
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = (
    "You are classifying a PDF document for a mining regulatory RAG system.\n\n"
    "First page text:\n"
    "---\n"
    "{first_page_text}\n"
    "---\n\n"
    "Classify this document's structural complexity. "
    "Call the classify_document tool with your assessment."
)
