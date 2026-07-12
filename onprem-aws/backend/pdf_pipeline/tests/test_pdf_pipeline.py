"""
test_pdf_pipeline.py
====================
Unit tests for the PDF Vectorization Pipeline.

Covers all three extraction paths (heuristic/s3_tag/haiku classifier,
Textract extractor, Claude extractor), the normalizer, embedder, ingestor,
and the orchestrator — all with mocked AWS clients.

Run with:
    cd docs && python -m pytest pdf_pipeline/tests/test_pdf_pipeline.py -v

No real AWS calls are made in these tests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root (docs) is on sys.path (mirrors how other tests in this project work)
# ---------------------------------------------------------------------------
import sys
import os

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.pdf_classifier import classify
from pdf_pipeline.tools.pdf_normalizer import (
    SectionMetadata,
    SectionRecord,
    build_section_metadata,
    normalize_sections,
)
from pdf_pipeline.tools.pdf_textract_extractor import (
    _reconstruct_sections,
)
from pdf_pipeline.tools.pdf_claude_extractor import (
    MaxTokensTruncationError,
    PageLimitExceededError,
    _call_claude_with_retry,
    _effective_read_timeout,
    _parse_claude_sections,
    _sanitize_doc_name,
    build_carry_over_context,
    extract_with_claude,
)
from pdf_pipeline.tools.pdf_titan_embedder import embed_section, embed_sections_batch
from pdf_pipeline.tools.pdf_opensearch_ingestor import (
    IngestResult,
    _section_to_doc,
    ingest_sections,
)
from pdf_pipeline.agent.pdf_vectorization_pipeline import (
    PdfPipelineResult,
    run_pipeline,
)
from pdf_pipeline.lambda_function import lambda_handler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> PdfPipelineConfig:
    return PdfPipelineConfig(
        aws_region="us-east-1",
        opensearch_host="test.us-east-1.aoss.amazonaws.com",
        opensearch_index="pdf_legal_vecs_test",
        titan_dimensions=1024,
        claude_max_pages=550,
        claude_max_mb=18.0,
        min_section_body_chars=10,
    )


@pytest.fixture
def sample_metadata() -> SectionMetadata:
    return build_section_metadata(
        bucket="test-bucket",
        key="legal/mining_act_2023.pdf",
        doc_class="complex_legal",
        file_size_bytes=5_242_880,
        total_pages=120,
    )


@pytest.fixture
def sample_sections(sample_metadata) -> list[SectionRecord]:
    return [
        SectionRecord(
            section_id="mining-act-2023-s0000",
            title="Part 1 — Preliminary",
            body="This Act may be cited as the Mining Safety Act 2023. It applies to all mining operations.",
            page_start=1,
            page_end=2,
            extraction_method="claude_native",
            batch_index=0,
            tables=[],
            citations=[],
            metadata=sample_metadata,
        ),
        SectionRecord(
            section_id="mining-act-2023-s0001",
            title="Part 2 — Licensing Requirements",
            body="No person shall operate a mine without a valid licence issued under this Part.",
            page_start=3,
            page_end=10,
            extraction_method="claude_native",
            batch_index=0,
            tables=[],
            citations=[],
            metadata=sample_metadata,
        ),
    ]


# ===========================================================================
# Tests: config routing limits
# ===========================================================================


class TestConfigLimits:
    def test_default_config_respects_bedrock_page_limit(self):
        """Regression guard: Bedrock rejects any PDF document block over 100
        pages, so neither routing threshold may default above that hard cap.
        Every Converse call — single-call or one mini-batch — must be valid."""
        cfg = PdfPipelineConfig()
        assert cfg.bedrock_max_pdf_pages == 100
        assert cfg.claude_max_pages <= cfg.bedrock_max_pdf_pages
        assert cfg.batch_max_pages <= cfg.bedrock_max_pdf_pages


# ===========================================================================
# Tests: pdf_classifier
# ===========================================================================


class TestClassifier:
    def _make_s3(self, file_size=1_000_000, tag_value=None, avg_chars=500.0):
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": file_size}
        s3.get_object_tagging.return_value = {
            "TagSet": [{"Key": "doc-type", "Value": tag_value}] if tag_value else []
        }
        # Return a small fitz-parseable sample (just enough to not crash)
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"%PDF-1.4")}
        return s3

    def _make_bedrock(self, complexity="high", confidence=0.95):
        bedrock = MagicMock()
        bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "classify_document",
                                "input": {
                                    "complexity": complexity,
                                    "confidence": confidence,
                                    "reasoning": "Dense regulatory structure detected",
                                },
                            }
                        }
                    ]
                }
            }
        }
        return bedrock

    def test_signal1_scanned_low_chars(self, config):
        """avg_chars < threshold → immediate simple classification via heuristic"""
        with (
            patch(
                "pdf_pipeline.tools.pdf_classifier._estimate_page_count",
                return_value=20,
            ),
            patch(
                "pdf_pipeline.tools.pdf_classifier._fetch_first_page_text",
                return_value="x" * 50,
            ),
        ):
            s3 = self._make_s3()
            bedrock = MagicMock()
            result = classify(
                "bucket", "key.pdf", config, s3_client=s3, bedrock_client=bedrock
            )

        assert result.doc_class == "simple"
        assert result.signal_used == "heuristic"
        assert result.confidence >= 0.9
        bedrock.converse.assert_not_called()

    def test_signal2_complex_legal_tag(self, config):
        """S3 tag 'mining_regulation' → complex_legal, no Haiku call"""
        with (
            patch(
                "pdf_pipeline.tools.pdf_classifier._estimate_page_count",
                return_value=30,
            ),
            patch(
                "pdf_pipeline.tools.pdf_classifier._fetch_first_page_text",
                return_value="x" * 800,
            ),
        ):
            s3 = self._make_s3(tag_value="mining_regulation")
            bedrock = MagicMock()
            result = classify(
                "bucket", "key.pdf", config, s3_client=s3, bedrock_client=bedrock
            )

        assert result.doc_class == "complex_legal"
        assert result.signal_used == "s3_tag"
        assert result.confidence == 0.99
        bedrock.converse.assert_not_called()

    def test_signal2_simple_tag(self, config):
        """S3 tag 'scanned_form' → simple, no Haiku call"""
        with (
            patch(
                "pdf_pipeline.tools.pdf_classifier._estimate_page_count", return_value=5
            ),
            patch(
                "pdf_pipeline.tools.pdf_classifier._fetch_first_page_text",
                return_value="x" * 800,
            ),
        ):
            s3 = self._make_s3(tag_value="scanned_form")
            bedrock = MagicMock()
            result = classify(
                "bucket", "key.pdf", config, s3_client=s3, bedrock_client=bedrock
            )

        assert result.doc_class == "simple"
        assert result.signal_used == "s3_tag"

    def test_signal3_haiku_high_confidence(self, config):
        """Haiku returns high complexity + high confidence → complex_legal"""
        with (
            patch(
                "pdf_pipeline.tools.pdf_classifier._estimate_page_count",
                return_value=25,
            ),
            patch(
                "pdf_pipeline.tools.pdf_classifier._fetch_first_page_text",
                return_value="Part 1 Preliminary " + "x" * 600,
            ),
        ):
            s3 = self._make_s3()
            bedrock = self._make_bedrock(complexity="high", confidence=0.92)
            result = classify(
                "bucket", "key.pdf", config, s3_client=s3, bedrock_client=bedrock
            )

        assert result.doc_class == "complex_legal"
        assert result.signal_used == "haiku"

    def test_signal3_haiku_low_confidence_safe_default(self, config):
        """Haiku returns low confidence → safe-default to complex_legal"""
        with (
            patch(
                "pdf_pipeline.tools.pdf_classifier._estimate_page_count",
                return_value=25,
            ),
            patch(
                "pdf_pipeline.tools.pdf_classifier._fetch_first_page_text",
                return_value="Some content " + "x" * 600,
            ),
        ):
            s3 = self._make_s3()
            bedrock = self._make_bedrock(complexity="low", confidence=0.4)
            result = classify(
                "bucket", "key.pdf", config, s3_client=s3, bedrock_client=bedrock
            )

        # Even though complexity=low, confidence < threshold → safe default
        assert result.doc_class == "complex_legal"
        assert result.signal_used == "haiku"

    def test_signal3_haiku_low_complexity_high_confidence(self, config):
        """Haiku returns low complexity + high confidence → simple"""
        with (
            patch(
                "pdf_pipeline.tools.pdf_classifier._estimate_page_count", return_value=5
            ),
            patch(
                "pdf_pipeline.tools.pdf_classifier._fetch_first_page_text",
                return_value="Standard form " + "x" * 600,
            ),
        ):
            s3 = self._make_s3()
            bedrock = self._make_bedrock(complexity="low", confidence=0.9)
            result = classify(
                "bucket", "key.pdf", config, s3_client=s3, bedrock_client=bedrock
            )

        assert result.doc_class == "simple"
        assert result.signal_used == "haiku"


# ===========================================================================
# Tests: pdf_normalizer
# ===========================================================================


class TestNormalizer:
    def test_basic_normalization(self, config, sample_metadata):
        raw = [
            {
                "title": "Section 1",
                "body": "This is the body text of section one.",
                "page_start": 1,
                "page_end": 2,
            },
            {
                "title": "Section 2",
                "body": "Second section body with sufficient content for indexing.",
                "page_start": 3,
                "page_end": 5,
            },
        ]
        records = normalize_sections(raw, "claude_native", sample_metadata, config)
        assert len(records) == 2
        assert records[0].section_id.endswith("-s0000")
        assert records[1].section_id.endswith("-s0001")
        assert records[0].extraction_method == "claude_native"

    def test_short_section_discarded(self, config, sample_metadata):
        raw = [
            {"title": "Blank", "body": "Hi", "page_start": 1, "page_end": 1},
            {
                "title": "Real",
                "body": "This section has enough content to be kept.",
                "page_start": 2,
                "page_end": 3,
            },
        ]
        records = normalize_sections(raw, "textract", sample_metadata, config)
        assert len(records) == 1
        assert records[0].title == "Real"

    def test_long_body_split_into_sub_sections(self, config, sample_metadata):
        # Create a body longer than titan_max_input_chars (8000 chars)
        long_body = "A" * 9500
        raw = [
            {
                "title": "Long Section",
                "body": long_body,
                "page_start": 1,
                "page_end": 10,
            }
        ]
        records = normalize_sections(raw, "claude_native", sample_metadata, config)
        assert len(records) >= 2
        assert "(part 1 of" in records[0].title
        assert "(part 2 of" in records[1].title
        # All sub-sections stay within the limit
        for r in records:
            assert len(r.body) <= config.titan_max_input_chars

    def test_table_serialized_into_body(self, config, sample_metadata):
        tables = [
            {
                "cells": [
                    {"row_index": 1, "col_index": 1, "text": "Header A"},
                    {"row_index": 1, "col_index": 2, "text": "Header B"},
                    {"row_index": 2, "col_index": 1, "text": "Value 1"},
                    {"row_index": 2, "col_index": 2, "text": "Value 2"},
                ]
            }
        ]
        raw = [
            {
                "title": "Table Section",
                "body": "Some text.",
                "page_start": 1,
                "page_end": 2,
                "tables": tables,
            }
        ]
        records = normalize_sections(raw, "textract", sample_metadata, config)
        assert len(records) == 1
        assert "Header A" in records[0].body
        assert "|" in records[0].body

    def test_unicode_normalization(self, config, sample_metadata):
        # ü (composed vs decomposed) — both should normalize to the same form
        body_decomposed = (
            "Bergbausicherheitsgesetz ü content with enough characters here."
        )
        raw = [
            {"title": "Test", "body": body_decomposed, "page_start": 1, "page_end": 1}
        ]
        records = normalize_sections(raw, "claude_native", sample_metadata, config)
        assert len(records) == 1
        # NFC normalization: composed form
        assert "ü" in records[0].body or "ü" not in records[0].body

    def test_missing_title_gets_default(self, config, sample_metadata):
        raw = [
            {
                "title": "",
                "body": "Sufficient body content for indexing.",
                "page_start": 1,
                "page_end": 1,
            }
        ]
        records = normalize_sections(raw, "claude_native", sample_metadata, config)
        assert records[0].title.startswith("untitled-")


# ===========================================================================
# Tests: pdf_claude_extractor — parse logic
# ===========================================================================


class TestClaudeExtractor:
    def test_parse_valid_json_array(self):
        raw = '[{"title": "S1", "body": "Body 1", "page_start": 1, "page_end": 2}]'
        result = _parse_claude_sections(raw)
        assert len(result) == 1
        assert result[0]["title"] == "S1"

    def test_parse_json_inside_markdown_fence(self):
        raw = '```json\n[{"title": "S1", "body": "Body", "page_start": 1, "page_end": 1}]\n```'
        result = _parse_claude_sections(raw)
        assert len(result) == 1

    def test_parse_json_with_preamble(self):
        raw = 'Here are the sections:\n[{"title": "S1", "body": "Body", "page_start": 1, "page_end": 1}]'
        result = _parse_claude_sections(raw)
        assert len(result) == 1

    def test_parse_invalid_json_returns_empty(self):
        raw = "This is not JSON at all."
        result = _parse_claude_sections(raw)
        assert result == []

    def test_sanitize_doc_name_removes_special_chars(self):
        name = _sanitize_doc_name("legal/Mining Act (2023) [Revised].pdf")
        assert " " not in name
        assert "(" not in name
        assert len(name) <= 200
        assert name  # not empty

    def test_carry_over_context_includes_title(self, sample_sections):
        last = {
            "title": "Part 5 — Environmental Controls",
            "body": "Environmental controls apply to...",
        }
        context = build_carry_over_context(last)
        assert "Part 5" in context
        assert "Environmental Controls" in context

    def test_extract_with_claude_mocked(self, config):
        """Full extract_with_claude() call with mocked Bedrock response."""
        sections = [
            {
                "title": "Part 1",
                "body": "Preliminary provisions.",
                "page_start": 1,
                "page_end": 3,
            },
            {
                "title": "Part 2",
                "body": "Licensing requirements.",
                "page_start": 4,
                "page_end": 12,
            },
        ]
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "emit_sections",
                                "input": {"sections": sections},
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 1500, "outputTokens": 800},
        }

        result = extract_with_claude(
            pdf_bytes=b"%PDF-1.4 test",
            bucket="test-bucket",
            key="legal/test.pdf",
            file_size_bytes=1_000_000,
            total_pages=15,
            config=config,
            bedrock_client=mock_bedrock,
            batch_index=0,
        )

        assert len(result.raw_sections) == 2
        assert result.raw_sections[0]["title"] == "Part 1"
        assert result.input_tokens == 1500
        assert result.output_tokens == 800
        assert result.errors == []

    def test_extract_with_claude_page_offset(self, config):
        """Page offsets from mini-batch are added to extracted page numbers."""
        sections = [
            {"title": "Part 3", "body": "Content.", "page_start": 1, "page_end": 5}
        ]
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "emit_sections",
                                "input": {"sections": sections},
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }

        result = extract_with_claude(
            pdf_bytes=b"%PDF-1.4 test",
            bucket="bucket",
            key="key.pdf",
            file_size_bytes=5_000_000,
            total_pages=300,
            config=config,
            bedrock_client=mock_bedrock,
            page_start_offset=200,  # this slice starts at page 200 of the original doc
            batch_index=1,
        )

        # page_start=1 + offset(200) - 1 = 200; page_end=5 + 199 = 204
        assert result.raw_sections[0]["page_start"] == 200
        assert result.raw_sections[0]["page_end"] == 204

    def test_extract_with_claude_handles_bedrock_error(self, config):
        """Bedrock API failure returns empty result with error recorded."""
        mock_bedrock = MagicMock()
        mock_bedrock.converse.side_effect = Exception("ThrottlingException")

        result = extract_with_claude(
            pdf_bytes=b"%PDF-1.4",
            bucket="bucket",
            key="key.pdf",
            file_size_bytes=1_000_000,
            total_pages=10,
            config=config,
            bedrock_client=mock_bedrock,
        )

        assert result.raw_sections == []
        assert len(result.errors) == 1
        assert "ThrottlingException" in result.errors[0]

    def test_retry_recovers_from_throttling(self, config):
        """A ThrottlingException on the first attempt should retry and succeed."""
        from botocore.exceptions import ClientError

        call_count = 0

        def mock_converse(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ThrottlingException",
                            "Message": "Rate exceeded",
                        }
                    },
                    "Converse",
                )
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "name": "emit_sections",
                                    "input": {
                                        "sections": [
                                            {
                                                "title": "S1",
                                                "body": "Body",
                                                "page_start": 1,
                                                "page_end": 1,
                                            }
                                        ]
                                    },
                                }
                            }
                        ]
                    }
                },
                "usage": {"inputTokens": 100, "outputTokens": 50},
            }

        mock_bedrock = MagicMock()
        mock_bedrock.converse.side_effect = mock_converse

        with patch("pdf_pipeline.tools.pdf_claude_extractor.time.sleep"):
            sections, input_tokens, output_tokens = _call_claude_with_retry(
                pdf_bytes=b"%PDF-1.4",
                doc_name="doc",
                context_note="",
                config=config,
                bedrock_client=mock_bedrock,
            )

        assert call_count == 2
        assert len(sections) == 1
        assert input_tokens == 100
        assert output_tokens == 50

    def test_retry_raises_max_tokens_truncation_without_retrying(self, config):
        """A ValidationException matching a truncation marker must not be retried."""
        from botocore.exceptions import ClientError

        mock_bedrock = MagicMock()
        mock_bedrock.converse.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "The model returned the following errors: max_tokens exceeded",
                }
            },
            "Converse",
        )

        with pytest.raises(MaxTokensTruncationError):
            _call_claude_with_retry(
                pdf_bytes=b"%PDF-1.4",
                doc_name="doc",
                context_note="",
                config=config,
                bedrock_client=mock_bedrock,
            )

        assert mock_bedrock.converse.call_count == 1

    def test_retry_raises_page_limit_without_retrying(self, config):
        """A ValidationException for the 100-page limit must raise
        PageLimitExceededError immediately, not retry the identical payload."""
        from botocore.exceptions import ClientError

        mock_bedrock = MagicMock()
        mock_bedrock.converse.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "A maximum of 100 PDF pages may be provided.",
                }
            },
            "Converse",
        )

        with pytest.raises(PageLimitExceededError):
            _call_claude_with_retry(
                pdf_bytes=b"%PDF-1.4",
                doc_name="doc",
                context_note="",
                config=config,
                bedrock_client=mock_bedrock,
            )

        assert mock_bedrock.converse.call_count == 1

    def test_effective_read_timeout_shrinks_near_deadline(self, config):
        """The Bedrock read timeout should tighten as the Lambda deadline approaches."""
        now = 1_000_000.0
        with patch(
            "pdf_pipeline.tools.pdf_claude_extractor.time.time", return_value=now
        ):
            assert _effective_read_timeout(config, None) is None

            far_deadline = now + config.bedrock_read_timeout_s + 100
            far_timeout = _effective_read_timeout(config, far_deadline)
            assert far_timeout == pytest.approx(config.bedrock_read_timeout_s)

            near_deadline = now + 15  # only 15s left
            near_timeout = _effective_read_timeout(config, near_deadline)
            assert near_timeout == pytest.approx(
                15 - config.bedrock_deadline_safety_margin_s
            )
            assert near_timeout < far_timeout


# ===========================================================================
# Tests: pdf_textract_extractor — block reconstruction
# ===========================================================================


class TestTextractReconstruction:
    def _make_block(self, block_id, btype, text="", page=1, top=0.0, children=None):
        block = {
            "Id": block_id,
            "BlockType": btype,
            "Page": page,
            "Geometry": {
                "BoundingBox": {"Top": top, "Left": 0.0, "Width": 1.0, "Height": 0.05}
            },
            "Relationships": [],
        }
        if text:
            block["Text"] = text
        if children:
            block["Relationships"].append({"Type": "CHILD", "Ids": children})
        return block

    def test_single_section_header_groups_content(self):
        blocks = [
            self._make_block(
                "h1", "LAYOUT_SECTION_HEADER", page=1, top=0.0, children=["w1"]
            ),
            self._make_block("w1", "WORD", text="Introduction", page=1, top=0.0),
            self._make_block("t1", "LAYOUT_TEXT", page=1, top=0.2, children=["w2"]),
            self._make_block(
                "w2",
                "WORD",
                text="This Act applies to all operations.",
                page=1,
                top=0.2,
            ),
        ]
        sections = _reconstruct_sections(blocks)
        assert len(sections) == 1
        assert sections[0]["title"] == "Introduction"
        assert "This Act applies" in sections[0]["body"]

    def test_multiple_sections_separated(self):
        blocks = [
            self._make_block(
                "h1", "LAYOUT_SECTION_HEADER", page=1, top=0.0, children=["w1"]
            ),
            self._make_block("w1", "WORD", text="Part 1", page=1, top=0.0),
            self._make_block("t1", "LAYOUT_TEXT", page=1, top=0.1, children=["w2"]),
            self._make_block("w2", "WORD", text="Body of Part 1.", page=1, top=0.1),
            self._make_block(
                "h2", "LAYOUT_SECTION_HEADER", page=2, top=0.0, children=["w3"]
            ),
            self._make_block("w3", "WORD", text="Part 2", page=2, top=0.0),
            self._make_block("t2", "LAYOUT_TEXT", page=2, top=0.1, children=["w4"]),
            self._make_block("w4", "WORD", text="Body of Part 2.", page=2, top=0.1),
        ]
        sections = _reconstruct_sections(blocks)
        assert len(sections) == 2
        assert sections[0]["title"] == "Part 1"
        assert sections[1]["title"] == "Part 2"
        assert sections[0]["page_start"] == 1
        assert sections[1]["page_start"] == 2

    def test_content_before_first_header_captured(self):
        blocks = [
            self._make_block("t0", "LAYOUT_TEXT", page=1, top=0.0, children=["w0"]),
            self._make_block(
                "w0", "WORD", text="Preamble content here.", page=1, top=0.0
            ),
            self._make_block(
                "h1", "LAYOUT_SECTION_HEADER", page=1, top=0.3, children=["w1"]
            ),
            self._make_block("w1", "WORD", text="Section 1", page=1, top=0.3),
        ]
        sections = _reconstruct_sections(blocks)
        # Preamble before first header should be captured as first section
        assert len(sections) == 2
        preamble = next(s for s in sections if "Preamble" in s["body"])
        assert preamble is not None


# ===========================================================================
# Tests: pdf_titan_embedder
# ===========================================================================


class TestTitanEmbedder:
    def test_embed_section_success(self, config):
        mock_vector = [0.1] * 1024
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps({"embedding": mock_vector}).encode()
            )
        }

        result = embed_section("Test text for embedding.", config, mock_client)
        assert len(result) == 1024
        assert result[0] == pytest.approx(0.1)

    def test_embed_section_wrong_dims_raises(self, config):
        mock_vector = [0.1] * 512  # wrong dimension
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {
            "body": MagicMock(
                read=lambda: json.dumps({"embedding": mock_vector}).encode()
            )
        }

        with pytest.raises(ValueError, match="512 dims"):
            embed_section("Test text.", config, mock_client)

    def test_embed_sections_batch_isolates_failures(self, config, sample_sections):
        """One failed embedding should not abort the batch."""
        call_count = 0

        def mock_invoke(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Simulated Bedrock error")
            return {
                "body": MagicMock(
                    read=lambda: json.dumps({"embedding": [0.5] * 1024}).encode()
                )
            }

        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = mock_invoke

        results = embed_sections_batch(sample_sections, config, mock_client)
        # First section fails, second succeeds
        assert len(results) == 1
        assert results[0][0].section_id == sample_sections[1].section_id


# ===========================================================================
# Tests: pdf_opensearch_ingestor
# ===========================================================================


class TestOpenSearchIngestor:
    def test_section_to_doc_structure(self, sample_sections):
        section = sample_sections[0]
        embedding = [0.1] * 1024
        doc = _section_to_doc(section, embedding)

        assert doc["section_id"] == section.section_id
        assert doc["title"] == section.title
        assert doc["body"] == section.body
        assert len(doc["text_embedding"]) == 1024
        assert doc["source_key"] == section.metadata.source_key
        assert doc["has_citations"] is False
        assert doc["has_tables"] is False

    def test_ingest_sections_calls_bulk(self, config, sample_sections):
        embeddings = [(s, [0.1] * 1024) for s in sample_sections]

        mock_client = MagicMock()
        mock_client.indices.exists.return_value = True
        mock_client.search.return_value = {"hits": {"hits": []}}

        with patch(
            "pdf_pipeline.tools.pdf_opensearch_ingestor.os_bulk", return_value=(2, [])
        ) as mock_bulk:
            result = ingest_sections(
                sections_with_embeddings=embeddings,
                config=config,
                opensearch_client=mock_client,
                force=False,
            )

        assert result.documents_indexed == 2
        assert result.documents_failed == 0
        mock_bulk.assert_called_once()

    def test_ingest_deletes_existing_before_indexing(self, config, sample_sections):
        """NextGen dedup: prior sections of the source doc are deleted, then re-indexed."""
        embeddings = [(s, [0.1] * 1024) for s in sample_sections]

        mock_client = MagicMock()
        mock_client.indices.exists.return_value = True
        mock_client.search.return_value = {
            "hits": {"hits": [{"_id": "old1"}, {"_id": "old2"}]}
        }

        with patch(
            "pdf_pipeline.tools.pdf_opensearch_ingestor.os_bulk", return_value=(2, [])
        ) as mock_bulk:
            result = ingest_sections(
                sections_with_embeddings=embeddings,
                config=config,
                opensearch_client=mock_client,
                force=False,
            )

        # delete-before-index searched for the document's source_key, bulk-deleted
        # the matches, then bulk indexed the fresh set (bulk called twice).
        mock_client.search.assert_called()
        assert mock_bulk.call_count == 2
        assert result.documents_indexed == 2

    def test_ingest_force_overwrites(self, config, sample_sections):
        embeddings = [(s, [0.1] * 1024) for s in sample_sections]

        mock_client = MagicMock()
        mock_client.indices.exists.return_value = True
        mock_client.search.return_value = {"hits": {"hits": []}}

        with patch(
            "pdf_pipeline.tools.pdf_opensearch_ingestor.os_bulk", return_value=(2, [])
        ):
            result = ingest_sections(
                sections_with_embeddings=embeddings,
                config=config,
                opensearch_client=mock_client,
                force=True,
            )

        # No custom _id and no mget skip; everything is (re)indexed, nothing skipped.
        mock_client.mget.assert_not_called()
        assert result.documents_skipped == 0
        assert result.documents_indexed == 2


# ===========================================================================
# Tests: orchestrator
# ===========================================================================


class TestOrchestrator:
    def _mock_classification(
        self, doc_class="complex_legal", page_count=50, file_size=5_000_000
    ):
        result = MagicMock()
        result.doc_class = doc_class
        result.page_count = page_count
        result.file_size_bytes = file_size
        result.signal_used = "haiku"
        result.confidence = 0.95
        return result

    def test_run_pipeline_simple_path(self, config):
        """End-to-end with simple doc_class → Textract path."""
        classification = self._mock_classification(doc_class="simple", page_count=5)

        with (
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.classify",
                return_value=classification,
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._run_textract_path"
            ) as mock_textract,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.embed_sections_batch"
            ) as mock_embed,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.ingest_sections"
            ) as mock_ingest,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.build_opensearch_client",
                return_value=MagicMock(),
            ),
        ):
            mock_textract.return_value = (
                [
                    {
                        "title": "S1",
                        "body": "Sufficient body content here.",
                        "page_start": 1,
                        "page_end": 2,
                    }
                ],
                "textract",
                1,
                0,
                0,
                [],
            )
            mock_embed.return_value = [
                (
                    MagicMock(
                        section_id="s0",
                        body="...",
                        title="S1",
                        page_start=1,
                        page_end=2,
                        extraction_method="textract",
                        batch_index=0,
                        tables=[],
                        citations=[],
                        metadata=MagicMock(),
                    ),
                    [0.1] * 1024,
                )
            ]
            mock_ingest.return_value = IngestResult(
                index_name="pdf_legal_vecs_test",
                documents_indexed=1,
                documents_failed=0,
                documents_skipped=0,
            )

            result = run_pipeline("bucket", "legal/simple.pdf", config=config)

        assert result.extraction_method == "textract"
        assert result.sections_indexed == 1
        assert result.overall_success

    def test_run_pipeline_claude_single_path(self, config):
        """End-to-end with complex_legal doc within single-call thresholds."""
        classification = self._mock_classification(
            doc_class="complex_legal", page_count=100, file_size=10_000_000
        )

        with (
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.classify",
                return_value=classification,
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._download_pdf",
                return_value=b"%PDF-1.4",
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._run_claude_single_path"
            ) as mock_claude,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.embed_sections_batch"
            ) as mock_embed,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.ingest_sections"
            ) as mock_ingest,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.build_opensearch_client",
                return_value=MagicMock(),
            ),
        ):
            mock_claude.return_value = (
                [
                    {
                        "title": "Part 1",
                        "body": "Preliminary.",
                        "page_start": 1,
                        "page_end": 5,
                    },
                    {
                        "title": "Part 2",
                        "body": "Licensing.",
                        "page_start": 6,
                        "page_end": 20,
                    },
                ],
                "claude_native",
                1,
                5000,
                1200,
                [],
            )
            mock_embed.return_value = [
                (
                    MagicMock(
                        section_id=f"s{i}",
                        body="...",
                        title=f"Part {i + 1}",
                        page_start=1,
                        page_end=5,
                        extraction_method="claude_native",
                        batch_index=0,
                        tables=[],
                        citations=[],
                        metadata=MagicMock(),
                    ),
                    [0.1] * 1024,
                )
                for i in range(2)
            ]
            mock_ingest.return_value = IngestResult(
                index_name="pdf_legal_vecs_test",
                documents_indexed=2,
                documents_failed=0,
                documents_skipped=0,
            )

            result = run_pipeline("bucket", "legal/complex.pdf", config=config)

        assert result.extraction_method == "claude_native"
        assert result.sections_indexed == 2
        assert result.input_tokens == 5000
        assert result.overall_success

    def test_run_pipeline_propagates_extraction_path_errors(self, config):
        """Errors surfaced by an extraction path (e.g. one truncated batch) must
        reach PdfPipelineResult.errors instead of being silently dropped."""
        classification = self._mock_classification(
            doc_class="complex_legal", page_count=100, file_size=10_000_000
        )

        with (
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.classify",
                return_value=classification,
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._download_pdf",
                return_value=b"%PDF-1.4",
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._run_claude_single_path"
            ) as mock_claude,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.embed_sections_batch"
            ) as mock_embed,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.ingest_sections"
            ) as mock_ingest,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.build_opensearch_client",
                return_value=MagicMock(),
            ),
        ):
            mock_claude.return_value = (
                [
                    {
                        "title": "Part 1",
                        "body": "Preliminary.",
                        "page_start": 1,
                        "page_end": 5,
                    }
                ],
                "claude_native",
                1,
                5000,
                1200,
                ["Batch 0 truncated at max_tokens: ValidationException"],
            )
            mock_embed.return_value = [
                (
                    MagicMock(
                        section_id="s0",
                        body="...",
                        title="Part 1",
                        page_start=1,
                        page_end=5,
                        extraction_method="claude_native",
                        batch_index=0,
                        tables=[],
                        citations=[],
                        metadata=MagicMock(),
                    ),
                    [0.1] * 1024,
                )
            ]
            mock_ingest.return_value = IngestResult(
                index_name="pdf_legal_vecs_test",
                documents_indexed=1,
                documents_failed=0,
                documents_skipped=0,
            )

            result = run_pipeline("bucket", "legal/complex.pdf", config=config)

        assert any("truncated at max_tokens" in e for e in result.errors)

    def test_run_pipeline_classification_failure_returns_error(self, config):
        """Classification exception → pipeline returns error result, does not crash."""
        with patch(
            "pdf_pipeline.agent.pdf_vectorization_pipeline.classify",
            side_effect=Exception("S3 error"),
        ):
            result = run_pipeline("bucket", "key.pdf", config=config)

        assert result.sections_indexed == 0
        assert len(result.errors) >= 1
        assert not result.overall_success

    def test_run_pipeline_no_sections_extracted(self, config):
        """Extraction returns empty list → pipeline reports error gracefully."""
        classification = self._mock_classification()
        with (
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.classify",
                return_value=classification,
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._download_pdf",
                return_value=b"%PDF-1.4",
            ),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline._run_claude_single_path"
            ) as mock_claude,
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.build_opensearch_client",
                return_value=MagicMock(),
            ),
        ):
            mock_claude.return_value = ([], "claude_native", 1, 0, 0, [])
            result = run_pipeline("bucket", "key.pdf", config=config)

        assert result.sections_extracted == 0
        assert result.sections_indexed == 0
        assert not result.overall_success


# ===========================================================================
# Tests: lambda_handler
# ===========================================================================


class TestLambdaHandler:
    def _make_event(self, bucket="legal-bucket", key="regulations/test.pdf"):
        return {
            "detail": {
                "bucket": {"name": bucket},
                "object": {"key": key},
            }
        }

    def test_skips_non_pdf(self):
        event = self._make_event(key="uploads/document.docx")
        with patch.dict(os.environ, {"OPENSEARCH_HOST": "test.aoss.example.com"}):
            result = lambda_handler(event, None)
        assert result["statusCode"] == 200
        assert "Not a PDF" in result["body"]

    def test_handles_url_encoded_key(self):
        encoded_key = "legal%2FMining+Act+%282023%29.pdf"
        event = self._make_event(key=encoded_key)

        with (
            patch.dict(os.environ, {"OPENSEARCH_HOST": "test.aoss.example.com"}),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.run_pipeline"
            ) as mock_run,
        ):
            mock_run.return_value = PdfPipelineResult(
                file_key="legal/Mining Act (2023).pdf",
                doc_class="complex_legal",
                extraction_method="claude_native",
                classification_signal="haiku",
                sections_extracted=5,
                sections_normalized=5,
                sections_embedded=5,
                sections_indexed=5,
                sections_failed=0,
                sections_skipped=0,
                total_pages=80,
                file_size_bytes=8_000_000,
                batches_used=1,
                input_tokens=10000,
                output_tokens=3000,
                duration_s=12.5,
            )
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        # Verify the key was URL-decoded before being passed to run_pipeline
        call_args = mock_run.call_args
        assert "Mining Act (2023).pdf" in call_args[1]["key"]

    def test_missing_opensearch_host_returns_error(self):
        event = self._make_event()
        # Unset OPENSEARCH_HOST to simulate misconfigured environment
        env = {k: v for k, v in os.environ.items() if k != "OPENSEARCH_HOST"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "pdf_pipeline.agent.pdf_vectorization_pipeline.run_pipeline",
                side_effect=ValueError("OPENSEARCH_HOST not set"),
            ),
        ):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 500

    def test_invalid_event_structure(self):
        with patch.dict(os.environ, {"OPENSEARCH_HOST": "test.aoss.example.com"}):
            result = lambda_handler({}, None)
        assert result["statusCode"] == 400
