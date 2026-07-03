"""
pdf_pipeline_settings.py
========================
Configuration dataclass for the PDF Vectorization Pipeline.

All routing thresholds, model IDs, and service parameters live here.
Override via environment variables or by passing a custom PdfPipelineConfig
instance to run_pipeline().

Required environment variables
-------------------------------
OPENSEARCH_HOST   — AOSS collection endpoint WITHOUT scheme or trailing slash
                    e.g. "abc123def456.us-east-1.aoss.amazonaws.com"

Optional environment variables
-------------------------------
AWS_REGION                     — default "us-east-1"
PDF_OPENSEARCH_INDEX           — default "pdf_legal_vecs"
PDF_ARTIFACT_BUCKET            — S3 bucket for intermediate artifacts
PDF_CLAUDE_MODEL_ID            — override Claude Sonnet model
PDF_HAIKU_MODEL_ID             — override Claude Haiku model
PDF_TITAN_MODEL_ID             — override Titan Embed model
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class PdfPipelineConfig:
    # ------------------------------------------------------------------
    # AWS
    # ------------------------------------------------------------------
    aws_region: str = field(
        default_factory=lambda: os.getenv("AWS_REGION", "us-east-1")
    )

    # ------------------------------------------------------------------
    # Classifier thresholds (Signal 1 — free heuristics)
    # ------------------------------------------------------------------
    # page_count > N  → route to TEXTRACT (likely scanned or low-complexity)
    scanned_page_threshold: int = 40
    # avg chars/page < N → route to TEXTRACT (low text density → scanned)
    avg_chars_threshold: int = 200
    # bytes to fetch from PDF tail to parse the xref for page count
    xref_tail_bytes: int = 2048

    # ------------------------------------------------------------------
    # Classifier thresholds (Signal 2 — S3 metadata tag)
    # ------------------------------------------------------------------
    s3_tag_key: str = "doc-type"
    complex_legal_tag_values: list = field(default_factory=lambda: [
        "legal_complex",
        "mining_regulation",
        "environmental_act",
        "safety_code",
        "regulatory_document",
    ])
    simple_tag_values: list = field(default_factory=lambda: [
        "simple_forms",
        "scanned_form",
        "standard_template",
        "low_complexity",
    ])

    # ------------------------------------------------------------------
    # Classifier thresholds (Signal 3 — Claude Haiku)
    # ------------------------------------------------------------------
    # Confidence below this → safe-default to complex_legal
    haiku_confidence_threshold: float = 0.7

    # ------------------------------------------------------------------
    # Routing thresholds
    # ------------------------------------------------------------------
    # Documents exceeding either limit use the mini-batch path
    claude_max_pages: int = 550
    claude_max_mb: float = 18.0
    # Each mini-batch must stay within both limits
    batch_max_pages: int = 500
    batch_max_mb: float = 15.0

    # ------------------------------------------------------------------
    # Claude Sonnet — extraction model
    # ------------------------------------------------------------------
    claude_model_id: str = field(
        default_factory=lambda: os.getenv(
            "PDF_CLAUDE_MODEL_ID",
            # Valid Bedrock identifier in this account (the CSV pipeline confirmed
            # this exact string returns 200). The previous "-20250514-v1:0" suffix
            # was a non-existent version and raised ValidationException.
            "us.anthropic.claude-sonnet-4-6",
        )
    )
    claude_max_tokens: int = 8192
    citations_enabled: bool = True

    # ------------------------------------------------------------------
    # Claude Haiku — classifier model (Signal 3)
    # ------------------------------------------------------------------
    claude_haiku_model_id: str = field(
        default_factory=lambda: os.getenv(
            "PDF_HAIKU_MODEL_ID",
            # Verified against the Bedrock console for this account. The previous
            # value was missing the "-v1:0" version suffix.
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )
    )
    haiku_max_tokens: int = 512

    # ------------------------------------------------------------------
    # Amazon Textract — simple path
    # ------------------------------------------------------------------
    textract_feature_types: list = field(
        default_factory=lambda: ["LAYOUT", "TABLES"]
    )
    textract_poll_interval_s: float = 5.0
    textract_max_poll_attempts: int = 120   # 10 min max

    # ------------------------------------------------------------------
    # Amazon Titan Embed v2 — embeddings
    # ------------------------------------------------------------------
    titan_model_id: str = field(
        default_factory=lambda: os.getenv(
            "PDF_TITAN_MODEL_ID",
            "amazon.titan-embed-text-v2:0",
        )
    )
    titan_dimensions: int = 1024
    titan_normalize: bool = True
    # Max chars per section body before sub-splitting; ~2000 tokens,
    # comfortably within Titan's 8192-token input limit
    titan_max_input_chars: int = 8_000

    # ------------------------------------------------------------------
    # OpenSearch Serverless (AOSS) — pdf_legal_vecs index
    # ------------------------------------------------------------------
    opensearch_host: str = field(
        default_factory=lambda: os.getenv("OPENSEARCH_HOST", "")
    )
    opensearch_index: str = field(
        default_factory=lambda: os.getenv("PDF_OPENSEARCH_INDEX", "pdf_legal_vecs")
    )
    opensearch_verify_certs: bool = field(
        default_factory=lambda: os.getenv(
            "OPENSEARCH_VERIFY_CERTS", "true"
        ).lower() == "true"
    )
    opensearch_bulk_batch_size: int = 50

    # ------------------------------------------------------------------
    # Intermediate S3 artifacts (optional; set for debugging / idempotency)
    # ------------------------------------------------------------------
    artifact_bucket: str = field(
        default_factory=lambda: os.getenv("PDF_ARTIFACT_BUCKET", "")
    )
    artifact_prefix: str = "pdf-vectorization"

    # ------------------------------------------------------------------
    # Embedding retry
    # ------------------------------------------------------------------
    embed_max_retries: int = 3
    embed_retry_base_s: float = 1.0

    # ------------------------------------------------------------------
    # Section normalizer
    # ------------------------------------------------------------------
    # Sections with fewer characters than this are discarded as noise
    min_section_body_chars: int = 50
    # Max title length stored in OpenSearch
    max_title_chars: int = 200
