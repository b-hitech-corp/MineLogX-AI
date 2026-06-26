# PDF Vectorization Pipeline — Adaptation Plan

**Date:** 2026-06-19
**Author:** MineLogX-AI Engineering
**Branch:** `feature/simple_rag`
**Based on:** Architecture diagram review, current `pdf_vectorizer_EC2.py` code analysis, and authoritative AWS documentation (Bedrock Converse API, Bedrock Citations API, Textract StartDocumentAnalysis, Titan Embed v2, OpenSearch Serverless).

---

## 1. Executive Summary

The current PDF pipeline (`docs/RAG/pdf_vectorizer_EC2.py`) is a prototype built on self-hosted infrastructure: PyMuPDF for text extraction, mxbai-embed-large on an EC2 Ollama server for embeddings, and S3 Vectors for storage. This approach has three fundamental limitations for production regulatory document processing:

1. **No routing intelligence.** Every PDF — regardless of whether it's a scanned form or a dense 400-page mining regulation — follows the same page-by-page fitz extraction path. Scanned documents produce empty or garbled text. Dense legal documents lose all semantic structure.
2. **No per-section extraction.** The pipeline chunks by character length or crude regex headings. It has no awareness of legal document structure: parts, divisions, schedules, clauses.
3. **Self-hosted infrastructure.** The EC2 Ollama server is a single point of failure, not horizontally scalable, and produces embeddings that are incompatible with the OpenSearch index the RAG agent will query.

The target architecture replaces all three of these with AWS-native services: a 3-signal classifier to route documents intelligently, Amazon Textract for scanned/simple documents, Amazon Bedrock Claude Sonnet 4 with native PDF input and Citations API for complex legal documents, Amazon Bedrock Titan Embed v2 for embeddings compatible with the `pdf_legal_vecs` OpenSearch index, and Amazon OpenSearch Serverless for vector storage.

**What does NOT change:** The existing CSV vectorization pipeline, the CSV opensearch_ingestor, the analytics pipeline, and the RAG agent skeleton. Those are out of scope.

---

## 2. Architecture Delta

### 2.1 Current State

```
S3 PDF
  └─► Download bytes (boto3)
        └─► split_pdf_into_chunks() [fitz, 2-page windows]
              └─► extract_text_with_fitz() [local PyMuPDF]
                    └─► split_text_into_embed_chunks() [length or regex-section]
                          └─► embed_text_with_mxbai() [HTTP → EC2 Ollama]
                                └─► s3vectors.put_vectors() [S3 Vectors]
```

Problems: no classification, no Textract for scanned docs, no semantic section awareness,
mxbai embeddings incompatible with OpenSearch, EC2 dependency.

### 2.2 Target State (from architecture diagram)

```
S3 PutObject
  └─► EventBridge Rule
        └─► Lambda: File Classification (3-signal)
              ├─► [simple]        Textract StartDocumentAnalysis (LAYOUT + TABLES)
              │                        └─► Lambda: Normalize + Chunk
              └─► [complex_legal] Bedrock Claude Sonnet 4 (native PDF + Citations API)
                    ├─► [≤550 pages, ≤18MB]  Single call
                    └─► [>550 pages OR >18MB] pdfplumber section scan
                                               └─► mini-batch sequential calls
                                                     └─► merge outputs
                                                           └─► Lambda: Normalize + Chunk
                                                                 └─► Bedrock Titan Embed v2
                                                                       └─► OpenSearch AOSS
                                                                             (pdf_legal_vecs)
```

### 2.3 Embedding Alignment

| Pipeline | Embedding model | Index |
|---|---|---|
| CSV vectorization | `cohere.embed-v4:0` | `minelogx-telemetry-v1` |
| PDF vectorization (target) | `amazon.titan-embed-text-v2:0` | `pdf_legal_vecs` |

The RAG agent must embed queries with **both models** to search both indices — a separate concern, but this plan locks in Titan Embed v2 as the canonical model for `pdf_legal_vecs`.

---

## 3. New Module Architecture

The refactored pipeline decomposes into eight focused modules plus a top-level orchestrator. All new files land under `docs/assets/` to match the existing project structure.

```
docs/assets/
  config/
    pdf_pipeline_settings.py        ← NEW: PdfPipelineConfig dataclass
  tools/
    pdf_classifier.py               ← NEW: 3-signal document classifier
    pdf_textract_extractor.py       ← NEW: simple path (Textract async)
    pdf_claude_extractor.py         ← NEW: complex path (Claude Sonnet native PDF)
    pdf_section_scanner.py          ← NEW: pdfplumber section boundary detection
    pdf_normalizer.py               ← NEW: unified section schema normalizer
    pdf_titan_embedder.py           ← NEW: Titan Embed v2 wrapper
    pdf_opensearch_ingestor.py      ← NEW: AOSS bulk ingestor for pdf_legal_vecs
  agent/
    pdf_vectorization_pipeline.py   ← NEW: top-level orchestrator
  tests/
    test_pdf_pipeline.py            ← NEW: unit + integration tests

docs/RAG/
  pdf_vectorizer_EC2.py             ← DEPRECATED (keep for reference, do not delete)
```

---

## 4. Unified Section Schema

Every extraction path — Textract, Claude single-call, Claude mini-batch — must produce the same output structure before it reaches the normalizer. This is the contract between extraction and embedding.

```python
# docs/assets/tools/pdf_normalizer.py

@dataclass
class SectionRecord:
    section_id: str           # "{sanitized_filename}-s{index}"
    title: str                # section heading, or "untitled-{n}" if absent
    body: str                 # full section text (normalized, whitespace cleaned)
    page_start: int           # 1-based
    page_end: int             # 1-based, inclusive
    extraction_method: str    # "textract" | "claude_native" | "claude_batch"
    batch_index: int          # 0 for single-call; batch number for mini-batch
    tables: list[dict]        # Textract table cells, empty list for claude paths
    citations: list[dict]     # Bedrock citations, empty list for textract path
    metadata: SectionMetadata

@dataclass
class SectionMetadata:
    source_bucket: str
    source_key: str
    doc_class: str            # "complex_legal" | "simple"
    file_size_bytes: int
    total_pages: int
    schema_version: str = "1.0"
```

**Why this matters:** The Titan embedder and OpenSearch ingestor operate only on `SectionRecord` objects. They are completely decoupled from how the text was extracted. Adding a new extraction path (e.g., Claude with extended thinking) requires no changes downstream of the normalizer.

---

## 5. Module Specifications

### 5.1 `pdf_pipeline_settings.py` — Configuration

```python
@dataclass
class PdfPipelineConfig:
    # AWS
    aws_region: str = "us-east-1"

    # Classifier thresholds
    scanned_page_threshold: int = 40          # page_count > N → TEXTRACT
    avg_chars_threshold: int = 200            # avg chars/page < N → TEXTRACT
    haiku_confidence_threshold: float = 0.7   # below this → CLAUDE SONNET (safe default)
    s3_tag_key: str = "doc-type"
    complex_legal_tag_values: list = field(default_factory=lambda: [
        "legal_complex", "mining_regulation", "environmental_act", "safety_code"
    ])
    simple_tag_values: list = field(default_factory=lambda: [
        "simple_forms", "scanned_form", "standard_template"
    ])

    # Routing thresholds
    claude_max_pages: int = 550
    claude_max_mb: float = 18.0
    batch_max_pages: int = 500
    batch_max_mb: float = 15.0

    # Claude Sonnet (extraction)
    claude_model_id: str = "us.anthropic.claude-sonnet-4-6-20250514-v1:0"
    claude_haiku_model_id: str = "us.anthropic.claude-haiku-4-5-20251001"
    claude_max_tokens: int = 8192
    citations_enabled: bool = True

    # Textract
    textract_feature_types: list = field(default_factory=lambda: ["LAYOUT", "TABLES"])
    textract_poll_interval_s: float = 5.0
    textract_max_poll_attempts: int = 120     # 10 min max

    # Titan Embed v2
    titan_model_id: str = "amazon.titan-embed-text-v2:0"
    titan_dimensions: int = 1024
    titan_normalize: bool = True
    titan_max_input_chars: int = 8_000        # ~2000 tokens, well within 8192 token limit

    # OpenSearch
    opensearch_host: str = ""                 # set via env var or direct
    opensearch_index: str = "pdf_legal_vecs"
    opensearch_bulk_batch_size: int = 50

    # Storage (intermediate artifacts)
    artifact_bucket: str = ""
    artifact_prefix: str = "pdf-vectorization"
```

**Engineering note:** Keep all numeric thresholds in config, never hardcoded in business logic. The routing thresholds (550 pages, 18MB, 500/15MB batch limits) are documented limits based on Bedrock Claude Sonnet's input constraints and practical batch stability — they must be easy to adjust as Bedrock limits evolve.

---

### 5.2 `pdf_classifier.py` — 3-Signal Document Classifier

**Responsibility:** Given an S3 key and object metadata, return `"complex_legal"` or `"simple"` without downloading the full PDF when possible.

**Signal cascade (sequential, short-circuit on decision):**

#### Signal 1: Free heuristics (no API calls)

Reads S3 object metadata (`HeadObject`) and the first 64KB of the PDF to compute:
- `page_count` from PDF cross-reference table (parseable from the raw bytes tail)
- `avg_chars_per_page` estimate from first-page fitz extraction on the 64KB sample
- `is_scanned` heuristic: if `/Image` objects dominate and text layer is near-zero

```python
@dataclass
class ClassificationResult:
    doc_class: str                   # "complex_legal" | "simple"
    confidence: float                # 0.0–1.0
    signal_used: str                 # "heuristic" | "s3_tag" | "haiku"
    page_count: int
    file_size_bytes: int
    avg_chars_per_page: float
    reasoning: str

def classify(
    bucket: str,
    key: str,
    config: PdfPipelineConfig,
    s3_client,
    bedrock_client,
) -> ClassificationResult:
    ...
```

**Decision tree implementation:**

```python
# Signal 1: heuristics
head = s3.head_object(Bucket=bucket, Key=key)
file_size = head["ContentLength"]
page_count = _estimate_page_count(bucket, key, s3)  # parse xref from PDF tail
avg_chars = _sample_avg_chars(bucket, key, s3)       # first page fitz sample

if _is_scanned(avg_chars) or page_count > config.scanned_page_threshold \
        or avg_chars < config.avg_chars_threshold:
    return ClassificationResult("simple", 0.95, "heuristic", ...)

# Signal 2: S3 tag
tags = s3.get_object_tagging(Bucket=bucket, Key=key)["TagSet"]
tag_map = {t["Key"]: t["Value"] for t in tags}
doc_type = tag_map.get(config.s3_tag_key, "")
if doc_type in config.complex_legal_tag_values:
    return ClassificationResult("complex_legal", 0.99, "s3_tag", ...)
if doc_type in config.simple_tag_values:
    return ClassificationResult("simple", 0.99, "s3_tag", ...)

# Signal 3: Claude Haiku (first page only, ~$0.0003/call)
first_page_text = _extract_first_page_text(bucket, key, s3)
haiku_result = _classify_with_haiku(first_page_text, bedrock_client, config)
if haiku_result.confidence < config.haiku_confidence_threshold:
    # safe default: route to Claude Sonnet rather than risk losing legal content
    return ClassificationResult("complex_legal", haiku_result.confidence, "haiku", ...)
return haiku_result
```

**Claude Haiku classification prompt:**

The Haiku call uses `tool_choice` with a `classify_document` tool for guaranteed structured output — the same pattern already proven in `schema_advisor.py`:

```python
CLASSIFY_TOOL = {
    "name": "classify_document",
    "description": "Classify a PDF document as complex legal or simple based on its first page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "complexity": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "high → complex legal document with dense regulatory structure; "
                               "low/medium → standard form, template, or low-density document"
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in this classification, 0.0–1.0"
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the classification decision"
            }
        },
        "required": ["complexity", "confidence", "reasoning"]
    }
}
```

**Cost note:** Signal 3 fires only when heuristics and tags are inconclusive. Haiku costs ~$0.0003 per first-page classification. This is negligible at scale.

---

### 5.3 `pdf_textract_extractor.py` — Simple Path

**Responsibility:** Extract structured text and tables from simple/scanned PDFs using Textract's asynchronous document analysis API.

**Key design decisions:**
- Uses `StartDocumentAnalysis` (async) rather than `AnalyzeDocument` (sync) because regulatory PDFs often exceed 1 page and the sync API is limited to single-page input from bytes.
- Feature types: `["LAYOUT", "TABLES"]`. LAYOUT preserves reading order and identifies headers/paragraphs/lists as semantic blocks. TABLES captures structured tabular data (e.g., emission limits, regulatory thresholds).
- Output: list of `SectionRecord` objects, grouped by LAYOUT's section headers.

```python
@dataclass
class TextractExtractionResult:
    sections: list[SectionRecord]
    job_id: str
    errors: list[str]

def extract_with_textract(
    bucket: str,
    key: str,
    file_size_bytes: int,
    total_pages: int,
    config: PdfPipelineConfig,
    textract_client,
    s3_client,
) -> TextractExtractionResult:
    """
    1. Start async job: StartDocumentAnalysis with LAYOUT + TABLES
    2. Poll GetDocumentAnalysis until SUCCEEDED or FAILED
    3. Paginate through all Block pages
    4. Reconstruct section boundaries from LAYOUT_SECTION_HEADER blocks
    5. Group WORD/LINE blocks under their nearest header
    6. Extract TABLE blocks and attach to the section they fall within
    7. Return list[SectionRecord]
    """
```

**Block reconstruction logic:**

Textract returns `Block` objects with types: `PAGE`, `LAYOUT_SECTION_HEADER`, `LAYOUT_TEXT`, `LAYOUT_LIST`, `TABLE`, `CELL`, `WORD`. The reconstruction traverses the block graph:

```
PAGE
  ├── LAYOUT_SECTION_HEADER (id: h1) → section boundary
  │     └── LINE → title text
  ├── LAYOUT_TEXT (id: t1) → body content for section above h1
  ├── TABLE (id: tb1)
  │     └── CELL → row/col data
  └── LAYOUT_SECTION_HEADER (id: h2) → next section boundary
```

Section grouping algorithm:
1. Iterate blocks in geometric order (top-to-bottom by `Geometry.BoundingBox.Top`).
2. Each `LAYOUT_SECTION_HEADER` opens a new section.
3. All subsequent content blocks until the next header are accumulated into the current section body.
4. `TABLE` blocks are serialized to a list of row dicts and stored in `SectionRecord.tables`.
5. Any content before the first header becomes an "untitled-0" preamble section.

**Polling pattern:**

```python
def _poll_textract_job(job_id: str, textract_client, config: PdfPipelineConfig) -> list[dict]:
    """Poll GetDocumentAnalysis with exponential backoff, paginate NextToken."""
    all_blocks = []
    next_token = None
    for attempt in range(config.textract_max_poll_attempts):
        params = {"JobId": job_id}
        if next_token:
            params["NextToken"] = next_token
        resp = textract_client.get_document_analysis(**params)
        status = resp["JobStatus"]
        if status == "FAILED":
            raise RuntimeError(f"Textract job {job_id} failed: {resp.get('StatusMessage')}")
        if status == "SUCCEEDED":
            all_blocks.extend(resp.get("Blocks", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return all_blocks
        time.sleep(config.textract_poll_interval_s)
    raise TimeoutError(f"Textract job {job_id} did not complete in time")
```

---

### 5.4 `pdf_section_scanner.py` — Section Boundary Map (Large Doc Preprocessing)

**Responsibility:** Scan the full PDF with pdfplumber to detect all section header positions and build a boundary map that allows the mini-batch slicer to cut cleanly at section boundaries.

This module is invoked **only** when the document is classified `complex_legal` AND exceeds the single-call threshold (>550 pages or >18MB).

```python
@dataclass
class SectionBoundary:
    title: str
    page_start: int    # 1-based
    char_offset: int   # approximate char offset within the full text for context summaries

@dataclass
class SectionMap:
    boundaries: list[SectionBoundary]
    total_pages: int
    total_chars: int

def scan_section_boundaries(
    pdf_bytes: bytes,
    heading_patterns: list[str] | None = None,
) -> SectionMap:
    """
    Use pdfplumber to scan the full PDF and extract section heading positions.

    Heading detection strategy (in order of priority):
    1. Font-size heuristic: text with font_size > median + 2pt, bold or all-caps
    2. Numbering pattern regex: "Part X", "Division Y", "Section Z.Z", "Schedule A"
    3. All-caps line at paragraph start

    Returns SectionMap with one boundary per detected heading.
    """
```

**Batch slicing logic (inside orchestrator):**

```python
def _build_batches(
    section_map: SectionMap,
    config: PdfPipelineConfig,
    pdf_bytes: bytes,
) -> list[tuple[int, int, bytes]]:
    """
    Slice pdf_bytes into (start_page, end_page, slice_bytes) tuples.
    Each slice must:
      - Stay within batch_max_pages (500) and batch_max_mb (15MB)
      - Start and end at a section boundary (never mid-section)
    """
```

**Batch context carry-over (in claude extractor):**

For every batch after the first, prepend a short context note to the Claude prompt:

```
[Context from previous batch]
Last section processed: "{last_section_title}"
Summary: {one_paragraph_summary}
Continue the extraction from the next section. Do not repeat content from the previous batch.
```

This prevents Claude from losing continuity across page-slice boundaries — a critical correctness requirement for large regulatory documents.

---

### 5.5 `pdf_claude_extractor.py` — Complex Path

**Responsibility:** Extract semantically structured sections from complex legal PDFs using Claude Sonnet 4's native PDF input via the Bedrock Converse API, with Citations API enabled.

This module handles both sub-paths:
- **Single call** (≤550 pages, ≤18MB)
- **Mini-batch** (>550 pages OR >18MB) — calls this same extraction function per batch, stitches results

**Key AWS API facts (confirmed from documentation):**
- Native PDF input: `document` content block in Converse API messages, `format: 'pdf'`, `source.bytes` (raw PDF bytes) or `source.s3Location` (S3 URI).
- Citations API: `citations: {'enabled': True}` on the document block. Response includes `CitationsContentBlock` with `content` (generated text) and `citations` (list of Citation objects with location, sourceContent, title).
- Available on: Claude Sonnet 4, Claude Opus 4, Claude Sonnet 3.7, Claude Sonnet 3.5v2.

```python
@dataclass
class ClaudeExtractionResult:
    sections: list[SectionRecord]
    input_tokens: int
    output_tokens: int
    errors: list[str]

def extract_with_claude(
    pdf_bytes: bytes,
    bucket: str,
    key: str,
    file_size_bytes: int,
    total_pages: int,
    page_start_offset: int = 0,    # for mini-batch: actual page number of this slice's first page
    batch_index: int = 0,
    context_note: str = "",         # carry-over from previous batch
    config: PdfPipelineConfig = None,
    bedrock_client = None,
) -> ClaudeExtractionResult:
```

**Bedrock Converse API call structure:**

```python
import base64

response = bedrock_client.converse(
    modelId=config.claude_model_id,
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "document": {
                        "format": "pdf",
                        "name": sanitize_doc_name(key),  # alphanumeric + hyphens only
                        "source": {
                            "bytes": pdf_bytes  # raw bytes, NOT base64-encoded
                        },
                        "citations": {
                            "enabled": config.citations_enabled
                        }
                    }
                },
                {
                    "text": _build_extraction_prompt(context_note)
                }
            ]
        }
    ],
    inferenceConfig={
        "maxTokens": config.claude_max_tokens,
        "temperature": 0.0
    }
)
```

**Extraction prompt:**

```python
def _build_extraction_prompt(context_note: str) -> str:
    return f"""
{f'[Context from previous batch]\n{context_note}\n\n' if context_note else ''}
You are a legal document analyst specializing in mining and environmental regulatory documents.

Extract every distinct section from this regulatory PDF as a structured list.
For each section:
- title: the exact section heading as it appears in the document
- body: the full section text, preserving all sub-clauses, numbered lists, and schedules
- page_start and page_end: the page numbers where this section begins and ends (1-based)

Rules:
- Do NOT summarize. Return the complete verbatim text of each section.
- Do NOT merge sections. Each heading in the document produces one entry.
- If a section contains sub-sections, include them all within the parent body.
- Tables should be represented as pipe-delimited text within the body.

Return a JSON array of section objects. Each object must have:
  "title" (string), "body" (string), "page_start" (int), "page_end" (int)

Output only the JSON array. No explanation, no markdown fencing.
""".strip()
```

**Citations handling:**

When `citations_enabled=True`, the response `content` field contains `CitationsContentBlock` objects instead of plain text. Each block has:
- `content`: list of generated text segments
- `citations`: list of Citation objects with `location.pageNumber`, `sourceContent` (the exact quoted passage), `title`

The normalizer stores citations in `SectionRecord.citations` as a list of dicts:
```python
{"page": int, "source_content": str, "title": str}
```

This enables citation-aware RAG responses (the RAG agent can surface "this answer comes from page 47, Section 3.2.1 of the Mining Safety Act").

**Response parsing:**

Claude returns a JSON array per the prompt. Parse with a try/except on `json.loads`, with a fallback that uses `re.search(r'\[.*\]', response_text, re.DOTALL)` to extract the array even if Claude inadvertently wraps it in markdown.

**Mini-batch merging (in orchestrator):**

After all batches complete, concatenate their `sections` lists in order. Do not re-sort; the section order is meaningful. Generate final `section_id` values sequentially across the merged list.

---

### 5.6 `pdf_normalizer.py` — Unified Section Schema Normalizer

**Responsibility:** Accept the raw output of any extraction path and produce a clean, consistent list of `SectionRecord` objects ready for embedding.

```python
def normalize_sections(
    raw_sections: list[dict],          # from textract OR claude extractor
    extraction_method: str,            # "textract" | "claude_native" | "claude_batch"
    metadata: SectionMetadata,
    config: PdfPipelineConfig,
) -> list[SectionRecord]:
```

**Normalization operations applied to every section:**

1. **Title cleaning:** strip leading/trailing whitespace, collapse internal whitespace, truncate to 200 chars.
2. **Body cleaning:** normalize Unicode (NFC), replace multiple newlines with double newline, strip page numbers and header/footer boilerplate (regex patterns for page number lines: `^\s*\d+\s*$`).
3. **Body length guard:** if `len(body) > config.titan_max_input_chars`, split the section into sub-sections using the same character-overlap algorithm from the existing codebase (reuse `chunk_text_by_length` from `pdf_vectorizer_EC2.py`). Each sub-section inherits the parent's title with a suffix `(part N of M)`.
4. **Empty section filtering:** discard sections where `len(body.strip()) < 50` — these are typically blank pages or repeated headers.
5. **`section_id` generation:** `f"{sanitized_filename}-s{index:04d}"` for deterministic, sortable IDs.
6. **Table serialization (Textract path):** convert table dicts to pipe-delimited markdown and append to the section body before embedding, so tables are searchable.

---

### 5.7 `pdf_titan_embedder.py` — Titan Embed v2 Wrapper

**Responsibility:** Embed a `SectionRecord.body` using Amazon Bedrock Titan Text Embeddings v2 and return the vector.

**Key API facts (confirmed from documentation):**
- Model ID: `amazon.titan-embed-text-v2:0`
- API: `bedrock_runtime.invoke_model(modelId=..., body=json.dumps({"inputText": ..., "dimensions": 1024, "normalize": True}))`
- Response: JSON with `"embedding"` key (list of floats)
- Supported dimensions: 256, 512, 1024 (use 1024 to match the OpenSearch `pdf_legal_vecs` index mapping)
- Input limit: ~8192 tokens (~32,000 characters). The normalizer enforces `titan_max_input_chars=8000` to stay comfortably within this.

```python
def embed_section(
    text: str,
    config: PdfPipelineConfig,
    bedrock_runtime_client,
) -> list[float]:
    """
    Embed text using Titan Embed v2 via Bedrock InvokeModel.
    Returns 1024-dimensional float32 vector.
    """
    body = json.dumps({
        "inputText": text,
        "dimensions": config.titan_dimensions,
        "normalize": config.titan_normalize,
    })
    response = bedrock_runtime_client.invoke_model(
        modelId=config.titan_model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    response_body = json.loads(response["body"].read())
    embedding = response_body["embedding"]
    if len(embedding) != config.titan_dimensions:
        raise ValueError(f"Titan returned {len(embedding)} dims, expected {config.titan_dimensions}")
    return embedding

def embed_sections_batch(
    sections: list[SectionRecord],
    config: PdfPipelineConfig,
    bedrock_runtime_client,
) -> list[tuple[SectionRecord, list[float]]]:
    """
    Embed all sections, with per-section error isolation.
    Failed sections are logged and excluded from results.
    """
```

**Retry policy:** Each `invoke_model` call wraps in a retry loop (3 attempts, exponential backoff starting at 1s) to handle Bedrock throttling (`ThrottlingException`). This is the same pattern the existing `opensearch_ingestor.py` uses for Cohere.

---

### 5.8 `pdf_opensearch_ingestor.py` — OpenSearch AOSS Ingestor

**Responsibility:** Bulk-index embedded `SectionRecord` objects into the `pdf_legal_vecs` index in Amazon OpenSearch Serverless.

**Architectural note:** This module is structurally parallel to the existing `opensearch_ingestor.py` (which handles CSV data into `minelogx-telemetry-v1`). Both use the same AOSS client pattern (boto3 `opensearchserverless` + `opensearch-py` with `AWSV4SignerAuth`). The key difference is the index mapping and the embedding model used to generate vectors.

**Index mapping for `pdf_legal_vecs`:**

```python
PDF_INDEX_MAPPING = {
    "settings": {
        "index.knn": True
    },
    "mappings": {
        "properties": {
            "section_id":         {"type": "keyword"},
            "title":              {"type": "text", "analyzer": "standard"},
            "body":               {"type": "text", "analyzer": "standard"},
            "text_embedding":     {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosine",
                    "engine": "nmslib"
                }
            },
            "source_bucket":      {"type": "keyword"},
            "source_key":         {"type": "keyword"},
            "doc_class":          {"type": "keyword"},
            "extraction_method":  {"type": "keyword"},
            "page_start":         {"type": "integer"},
            "page_end":           {"type": "integer"},
            "batch_index":        {"type": "integer"},
            "total_pages":        {"type": "integer"},
            "file_size_bytes":    {"type": "long"},
            "schema_version":     {"type": "keyword"},
            "has_citations":      {"type": "boolean"},
            "has_tables":         {"type": "boolean"},
            "indexed_at":         {"type": "date"}
        }
    }
}
```

**Document structure:**

```python
def _section_to_doc(section: SectionRecord, embedding: list[float]) -> dict:
    return {
        "section_id": section.section_id,
        "title": section.title,
        "body": section.body,
        "text_embedding": embedding,
        "source_bucket": section.metadata.source_bucket,
        "source_key": section.metadata.source_key,
        "doc_class": section.metadata.doc_class,
        "extraction_method": section.extraction_method,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "batch_index": section.batch_index,
        "total_pages": section.metadata.total_pages,
        "file_size_bytes": section.metadata.file_size_bytes,
        "schema_version": section.metadata.schema_version,
        "has_citations": bool(section.citations),
        "has_tables": bool(section.tables),
        "indexed_at": datetime.now(timezone.utc).isoformat()
    }
```

**Idempotency:** Before indexing, check if `section_id` already exists (`GET /{index}/_doc/{section_id}`). If it does and `force=False`, skip. If `force=True`, overwrite via PUT. This mirrors the pattern in `csv_vectorization_pipeline.py`.

**Bulk ingest:**

```python
def ingest_sections(
    sections_with_embeddings: list[tuple[SectionRecord, list[float]]],
    config: PdfPipelineConfig,
    opensearch_client,
    force: bool = False,
) -> IngestResult:
    """Bulk index sections in batches of config.opensearch_bulk_batch_size."""
```

---

### 5.9 `pdf_vectorization_pipeline.py` — Top-Level Orchestrator

**Responsibility:** Coordinate all modules into a single `run_pipeline(bucket, key)` call. This is the Lambda handler entrypoint.

```python
@dataclass
class PdfPipelineResult:
    file_key: str
    doc_class: str
    extraction_method: str
    sections_extracted: int
    sections_indexed: int
    sections_failed: int
    classification_signal: str
    total_pages: int
    batches_used: int        # 1 for single-call, N for mini-batch
    input_tokens: int        # Claude tokens consumed (0 for textract path)
    output_tokens: int
    duration_s: float
    errors: list[str]

def run_pipeline(
    bucket: str,
    key: str,
    config: PdfPipelineConfig | None = None,
    force: bool = False,
) -> PdfPipelineResult:
```

**Full orchestration flow:**

```
1. Classify document (pdf_classifier.py)
   ├── doc_class = "simple"
   │     └── Extract via Textract (pdf_textract_extractor.py)
   └── doc_class = "complex_legal"
         ├── file_size ≤ 18MB AND page_count ≤ 550
         │     └── Download full PDF bytes
         │           └── Extract via Claude single call (pdf_claude_extractor.py)
         └── file_size > 18MB OR page_count > 550
               ├── Download full PDF bytes
               ├── Scan section boundaries (pdf_section_scanner.py)
               ├── Build batches ≤ 500 pages / 15MB at section boundaries
               └── For each batch (sequential):
                     ├── Extract via Claude (pdf_claude_extractor.py, batch_index=N)
                     │     └── Inject carry-over context for N > 0
                     └── Collect ClaudeExtractionResult

2. Normalize all sections (pdf_normalizer.py)
   └── list[SectionRecord]

3. Embed all sections (pdf_titan_embedder.py)
   └── list[tuple[SectionRecord, list[float]]]

4. Ingest into OpenSearch (pdf_opensearch_ingestor.py)
   └── PdfIngestResult

5. Return PdfPipelineResult
```

**Lambda handler wrapper:**

```python
def lambda_handler(event: dict, context) -> dict:
    """
    EventBridge event from S3 PutObject rule.
    event["detail"]["bucket"]["name"] → bucket
    event["detail"]["object"]["key"] → key
    """
    bucket = event["detail"]["bucket"]["name"]
    key = urllib.parse.unquote_plus(event["detail"]["object"]["key"])

    if not key.lower().endswith(".pdf"):
        return {"statusCode": 200, "body": "Not a PDF, skipping"}

    config = PdfPipelineConfig(
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        opensearch_host=os.environ["OPENSEARCH_HOST"],
        artifact_bucket=os.environ.get("ARTIFACT_BUCKET", bucket),
    )

    result = run_pipeline(bucket, key, config)
    return {
        "statusCode": 200 if not result.errors else 207,
        "body": json.dumps(asdict(result))
    }
```

---

## 6. Implementation Phases

### Phase 1 — Configuration and Classifier (2 days)

**Files:**
- `docs/assets/config/pdf_pipeline_settings.py`
- `docs/assets/tools/pdf_classifier.py`
- `docs/assets/tests/test_pdf_pipeline.py` (classifier unit tests)

**Deliverables:**
- `PdfPipelineConfig` with all documented thresholds
- `classify()` function passing all three signals
- Unit tests: mock S3 `head_object`, mock Haiku response, verify each signal short-circuits correctly
- Validate Haiku `tool_choice` call returns deterministic classification

**Key risk:** The first-page fitz sample for heuristics requires downloading ~64KB of the PDF. Confirm this is sufficient for page count estimation (PDF xref tables are at the end of the file, not the beginning). Alternative: use `HeadObject` for size, and call a cheap fitz open on a 2KB tail sample to read the cross-reference for page count.

---

### Phase 2 — Simple Path: Textract Extractor (2 days)

**Files:**
- `docs/assets/tools/pdf_textract_extractor.py`
- `docs/assets/tools/pdf_normalizer.py` (Textract-specific normalization first)

**Deliverables:**
- `extract_with_textract()` with async poll loop
- Block reconstruction: `LAYOUT_SECTION_HEADER` → section boundary grouping
- Table serialization: Textract CELL blocks → pipe-delimited markdown
- Normalizer first pass (Textract path only)
- Integration test: process a sample simple form PDF, assert ≥1 section returned

**Key risk:** Textract `StartDocumentAnalysis` with LAYOUT requires the PDF to be in S3 (not passed as bytes). The current code downloads to memory. For the Textract path, the source S3 key is passed directly — no download needed. This also avoids Lambda memory limits on large scanned PDFs.

---

### Phase 3 — Complex Path: Claude Extractor + Section Scanner (3 days)

**Files:**
- `docs/assets/tools/pdf_claude_extractor.py`
- `docs/assets/tools/pdf_section_scanner.py`
- `docs/assets/tools/pdf_normalizer.py` (Claude-specific normalization, citations)

**Deliverables:**
- `extract_with_claude()` — single-call path with Bedrock Converse API
- Citations response parsing: `CitationsContentBlock` → `SectionRecord.citations`
- `scan_section_boundaries()` — pdfplumber heading detection
- `_build_batches()` — section-boundary-aware slicing
- Mini-batch sequential loop with carry-over context
- Batch result merging
- Normalizer second pass (Claude path, body length guard, sub-section splitting)
- Integration test: process a 20-page sample regulatory PDF, assert section titles match expected headings

**Key risk:** Claude's JSON output fidelity. Even at `temperature=0.0`, Claude may occasionally output malformed JSON for very long documents. Add a `json_repair` fallback (the `json-repair` PyPI library) before raising a parse error. If repair also fails, log the raw output and mark the batch as failed — do not silently swallow the error.

---

### Phase 4 — Embedding and Ingestor (2 days)

**Files:**
- `docs/assets/tools/pdf_titan_embedder.py`
- `docs/assets/tools/pdf_opensearch_ingestor.py`
- `docs/assets/config/pdf_pipeline_settings.py` (finalize OpenSearch config)

**Deliverables:**
- `embed_section()` and `embed_sections_batch()` with Titan Embed v2
- Retry logic for Bedrock `ThrottlingException`
- `pdf_legal_vecs` index creation (if not exists)
- Bulk indexer with idempotency check
- `IngestResult` dataclass
- Integration test: embed 3 mock sections, verify 1024-dim vectors, index into a test AOSS collection

---

### Phase 5 — Orchestrator and End-to-End Test (2 days)

**Files:**
- `docs/assets/agent/pdf_vectorization_pipeline.py`
- `docs/assets/tests/test_pdf_pipeline.py` (end-to-end tests)

**Deliverables:**
- `run_pipeline()` integrating all modules
- `lambda_handler()` with EventBridge S3 event parsing
- `PdfPipelineResult` with full observability fields
- End-to-end test: S3 → classify → extract (mock Textract and Claude) → normalize → embed (mock Titan) → index (mock AOSS) → assert `PdfPipelineResult.sections_indexed > 0`
- Manual smoke test: process one real regulatory PDF from the existing S3 bucket

---

## 7. OpenSearch Index Setup

The `pdf_legal_vecs` index must be created before the first document is indexed. Add `ensure_index_exists()` to `pdf_opensearch_ingestor.py` (same pattern as the CSV ingestor):

```python
def ensure_index_exists(client, config: PdfPipelineConfig) -> bool:
    if not client.indices.exists(index=config.opensearch_index):
        client.indices.create(index=config.opensearch_index, body=PDF_INDEX_MAPPING)
        logger.info("Created index: %s", config.opensearch_index)
        return True
    return False
```

Call this at the start of every `run_pipeline()` execution. OpenSearch will no-op if the index already exists.

**AOSS network policy:** The Lambda function must run in the same VPC as the AOSS collection, or the AOSS collection must have a data access policy allowing the Lambda execution role. This is the same setup already used by the CSV pipeline — reuse the existing IAM role and VPC config.

---

## 8. IAM Permissions Required

The Lambda execution role needs:

```json
{
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:HeadObject", "s3:GetObjectTagging"], "Resource": "arn:aws:s3:::SOURCE_BUCKET/*"},
    {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": [
      "arn:aws:bedrock:*::foundation-model/us.anthropic.claude-sonnet-4-6-20250514-v1:0",
      "arn:aws:bedrock:*::foundation-model/us.anthropic.claude-haiku-4-5-20251001",
      "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0"
    ]},
    {"Effect": "Allow", "Action": ["textract:StartDocumentAnalysis", "textract:GetDocumentAnalysis"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["aoss:APIAccessAll"], "Resource": "arn:aws:aoss:*:*:collection/PDF_COLLECTION_ID"}
  ]
}
```

---

## 9. What to Preserve from `pdf_vectorizer_EC2.py`

The current file should be kept as-is (deprecated, not deleted). The following functions can be **directly reused** in the new modules:

| Function | Reuse in |
|---|---|
| `sanitize_filename()` | `pdf_vectorization_pipeline.py` |
| `list_s3_folders()` | `pdf_vectorization_pipeline.py` (batch mode) |
| `list_s3_files()` | `pdf_vectorization_pipeline.py` (batch mode) |
| `chunk_text_by_length()` | `pdf_normalizer.py` (body length guard) |
| `clear_vector_index()` | Adapted for OpenSearch `delete_by_query` |
| `compress_pdf_chunk()` | `pdf_section_scanner.py` (if batch slice exceeds 15MB) |

**What is NOT reused:**
- `extract_text_with_fitz()` — replaced by Textract (simple) and Claude native PDF (complex)
- `embed_text_with_mxbai()` — replaced by Titan Embed v2
- `process_pdf_to_vectors()` — replaced by `run_pipeline()`
- `split_pdf_into_chunks()` — replaced by section-boundary-aware `_build_batches()`
- The S3 Vectors storage layer entirely

---

## 10. Testing Strategy

### Unit tests (no AWS calls)

| Test | Mocks | Asserts |
|---|---|---|
| Classifier heuristics: scanned doc | Mock S3 HeadObject, fitz with 10 chars/page | Returns `"simple"`, signal=`"heuristic"` |
| Classifier heuristics: dense legal | Mock S3 HeadObject, fitz with 500 chars/page, 30 pages | Falls through to S3 tag or Haiku |
| Classifier S3 tag: `legal_complex` | Mock GetObjectTagging | Returns `"complex_legal"`, signal=`"s3_tag"` |
| Classifier Haiku: high confidence | Mock Bedrock Converse response | Returns `"complex_legal"`, signal=`"haiku"` |
| Classifier Haiku: low confidence | Mock Bedrock, confidence=0.5 | Returns `"complex_legal"` (safe default) |
| Textract block reconstruction | Real Textract GetDocumentAnalysis fixture | Correct section count, no empty sections |
| Normalizer body length guard | Section with 12,000-char body | Produces 2 sub-sections with `(part 1 of 2)` suffix |
| Titan embedder dims | Mock invoke_model returning 1024 floats | No ValueError raised |
| Claude JSON parse fallback | Mock response with markdown-wrapped JSON | Extracts array correctly |

### Integration tests (real AWS, controlled fixtures)

| Test | Fixture | Assert |
|---|---|---|
| Textract full path | 5-page scanned form in test S3 bucket | ≥1 section, no empty body |
| Claude single-call path | 30-page mining regulation PDF | Sections match known headings |
| Claude mini-batch path | Synthesized PDF >550 pages | Section continuity across batch boundary |
| Titan → OpenSearch round-trip | 3 mock sections | Queryable via kNN search |
| Full pipeline end-to-end | Real regulatory PDF from existing S3 bucket | `PdfPipelineResult.sections_indexed ≥ 5` |

### Regression test

After the new pipeline processes the same PDFs that were previously vectorized with the EC2/mxbai approach, run a parallel RAG query against both the old S3 Vectors index and the new OpenSearch `pdf_legal_vecs` index with 5 regulatory questions. Assert that the new pipeline retrieves ≥ as many relevant sections as the old one. The old index is not deleted until this comparison passes.

---

## 11. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Claude returns malformed JSON for large sections | Medium | Medium | `json-repair` fallback; log raw response on failure |
| Textract LAYOUT misses section headers in unusual fonts | Medium | Medium | Fall back to regex-based heading detection on LAYOUT_TEXT blocks if LAYOUT_SECTION_HEADER count = 0 |
| Bedrock ThrottlingException during batch embedding | High (large batches) | Low | Retry with exponential backoff; process sections sequentially not in parallel |
| pdfplumber crashes on malformed PDF bytes | Low | High | Wrap in try/except; fall back to single Claude call if section scan fails |
| Claude mini-batch loses context across boundary | Medium | High | Context carry-over prompt (last section title + summary) is mandatory, not optional |
| AOSS VPC connectivity from Lambda | Low (already solved for CSV) | High | Reuse existing CSV pipeline VPC config and security group |
| Titan dims mismatch with OpenSearch mapping | Low | High | Assert `len(embedding) == config.titan_dimensions` immediately after invoke_model |
| Large PDF (>18MB) download in Lambda memory | Medium | Medium | Lambda memory ≥ 3GB; for very large PDFs pass S3 URI directly to Claude's `source.s3Location` |

---

## 12. Key Engineering Decisions

### Why tool_choice for Haiku classification, not raw generation?
The same reason it's used in `schema_advisor.py`: guaranteed structured output with `confidence` as a float. Without tool_choice, Haiku might output "I think this is complex..." and require fragile string parsing.

### Why pdfplumber for section scanning, not fitz?
pdfplumber exposes font size, bold, and text style attributes directly — critical for heading detection in legal documents where headings are distinguished by typography, not markdown syntax. fitz's `get_text("blocks")` does not expose font weight.

### Why sequential Claude mini-batch calls, not parallel?
Continuity. Section carry-over context requires batch N-1 to complete before batch N starts. Parallelizing batches would cause each batch to start without knowledge of the previous, producing duplicate or discontinuous sections at batch boundaries.

### Why Titan Embed v2 and not Cohere for PDF?
The PDF pipeline is a separate retrieval domain from the CSV telemetry data. Using different embedding models for different domains is intentional — the RAG agent will embed PDF queries with Titan and CSV queries with Cohere, searching each index with the correct model. Cross-domain contamination in a single index with a single embedding model would produce poor retrieval quality for both document types.

### Why store `body` as text in OpenSearch alongside the vector?
Hybrid search. OpenSearch AOSS supports BM25 (keyword) + kNN (vector) hybrid queries. Storing the full body text enables BM25 relevance scoring on exact regulatory terms (specific chemical names, regulation numbers) that may not be well-represented in semantic space.
