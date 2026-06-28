"""
pdf_titan_embedder.py
=====================
Titan Embed v2 wrapper for the PDF Vectorization Pipeline.

Generates 1024-dimensional float32 embeddings for SectionRecord objects using
Amazon Bedrock Titan Text Embeddings V2 (amazon.titan-embed-text-v2:0).

The embedding is computed on SectionRecord.body (the cleaned, normalized section
text). The normalizer enforces titan_max_input_chars ≤ 8000 characters per
section, keeping all inputs well within Titan's 8192-token limit.

Retry policy: 3 attempts with exponential backoff starting at 1s, handling
Bedrock ThrottlingException. Failed sections are isolated — one failure does
not abort the batch.

Public API
----------
    embed_section(text, config, bedrock_runtime_client) -> list[float]
    embed_sections_batch(sections, config, bedrock_runtime_client)
        -> list[tuple[SectionRecord, list[float]]]
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.pdf_normalizer import SectionRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core embedding call
# ---------------------------------------------------------------------------

def embed_section(
    text: str,
    config: PdfPipelineConfig,
    bedrock_runtime_client: Any,
) -> list[float]:
    """Embed a single text string using Titan Text Embeddings V2.

    Args:
        text: Section body text to embed. Must be within titan_max_input_chars.
        config: PdfPipelineConfig with model ID and dimension settings.
        bedrock_runtime_client: Reusable boto3 bedrock-runtime client.

    Returns:
        List of 1024 float values (the embedding vector).

    Raises:
        RuntimeError: If all retry attempts fail.
        ValueError: If the returned vector dimension does not match config.titan_dimensions.
    """
    body = json.dumps({
        "inputText": text,
        "dimensions": config.titan_dimensions,
        "normalize": config.titan_normalize,
    })

    last_exc: Exception | None = None
    for attempt in range(config.embed_max_retries):
        try:
            response = bedrock_runtime_client.invoke_model(
                modelId=config.titan_model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            response_body = json.loads(response["body"].read())
            embedding = response_body.get("embedding", [])

            if not embedding:
                raise ValueError("Titan returned empty embedding")

            if len(embedding) != config.titan_dimensions:
                raise ValueError(
                    f"Titan returned {len(embedding)} dims; expected {config.titan_dimensions}"
                )

            return embedding

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ThrottlingException":
                wait = config.embed_retry_base_s * (2 ** attempt)
                logger.warning(
                    "Titan ThrottlingException (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, config.embed_max_retries, wait,
                )
                time.sleep(wait)
                last_exc = exc
                continue
            raise RuntimeError(f"Titan invoke_model failed: {exc}") from exc

        except ValueError:
            # Validation failures (empty / wrong-dimension embedding) are
            # caller-facing contract errors — propagate as ValueError, never
            # wrap as RuntimeError or retry.
            raise

        except Exception as exc:
            raise RuntimeError(f"Titan embedding failed: {exc}") from exc

    raise RuntimeError(
        f"Titan embedding failed after {config.embed_max_retries} retries"
    ) from last_exc


# ---------------------------------------------------------------------------
# Batch embedding
# ---------------------------------------------------------------------------

def embed_sections_batch(
    sections: list[SectionRecord],
    config: PdfPipelineConfig,
    bedrock_runtime_client: Any | None = None,
) -> list[tuple[SectionRecord, list[float]]]:
    """Embed all sections with per-section error isolation.

    Sections that fail to embed are logged and excluded from the returned list.
    The caller (orchestrator) decides how to handle partial failures.

    Args:
        sections: List of SectionRecord objects to embed.
        config: PdfPipelineConfig.
        bedrock_runtime_client: Reusable boto3 bedrock-runtime client.

    Returns:
        List of (SectionRecord, embedding) tuples for successfully embedded sections.
    """
    client = bedrock_runtime_client or boto3.client(
        "bedrock-runtime", region_name=config.aws_region
    )

    results: list[tuple[SectionRecord, list[float]]] = []
    failed = 0

    for idx, section in enumerate(sections):
        try:
            # Guard: truncate if the normalizer somehow let a long body through
            text = section.body
            if len(text) > config.titan_max_input_chars:
                logger.warning(
                    "Section %s body (%d chars) exceeds titan_max_input_chars=%d — truncating",
                    section.section_id, len(text), config.titan_max_input_chars,
                )
                text = text[: config.titan_max_input_chars]

            embedding = embed_section(text, config, client)
            results.append((section, embedding))

            if (idx + 1) % 10 == 0:
                logger.info("Embedded %d/%d sections", idx + 1, len(sections))

        except Exception as exc:
            logger.error(
                "Failed to embed section %s: %s", section.section_id, exc, exc_info=True
            )
            failed += 1

    logger.info(
        "Embedding complete: %d succeeded, %d failed (total=%d)",
        len(results), failed, len(sections),
    )
    return results
