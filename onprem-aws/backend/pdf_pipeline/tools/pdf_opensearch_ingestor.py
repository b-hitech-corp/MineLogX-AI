"""
pdf_opensearch_ingestor.py
==========================
OpenSearch Serverless (AOSS) ingestor for the PDF Vectorization Pipeline.

Bulk-indexes SectionRecord objects with their Titan Embed v2 vectors into
the pdf_legal_vecs index in Amazon OpenSearch Serverless.

Index design:
  - knn_vector (1024 dims, cosine, HNSW) for semantic ANN retrieval
  - text + title as plain text fields for BM25 hybrid search
  - Rich metadata fields (source_key, doc_class, page ranges, extraction method)
    to support filtered queries and provenance tracing in RAG responses
  - has_citations and has_tables boolean flags for routing in the RAG agent

Authentication: IAM / SigV4 (service='aoss') — same as the CSV pipeline.
The Lambda execution role needs aoss:APIAccessAll on the collection.

AOSS NextGen knn_vector note:
  NextGen collections auto-select the HNSW configuration and REJECT an explicit
  engine/method ("Field parameter 'engine' is not supported"). The mapping
  therefore declares only type + dimension. Titan vectors are normalized, so the
  default space yields kNN ranking equivalent to cosine.

Public API
----------
    ensure_index_exists(client, config) -> bool
    ingest_sections(sections_with_embeddings, config, opensearch_client, force) -> IngestResult
    build_opensearch_client(config) -> OpenSearch
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
from opensearchpy.helpers import bulk as os_bulk

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.pdf_normalizer import SectionRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index mapping
# ---------------------------------------------------------------------------

PDF_INDEX_MAPPING = {
    "settings": {"index.knn": True},
    "mappings": {
        "properties": {
            "section_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "standard",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            "body": {"type": "text", "analyzer": "standard"},
            # AOSS NextGen rejects an explicit engine/method ("Field parameter
            # 'engine' is not supported") and auto-selects the HNSW config — so we
            # specify only type + dimension, matching the working CSV index mapping.
            # Titan vectors are normalized (titan_normalize), so cosine vs the
            # default space gives equivalent kNN ranking.
            "text_embedding": {
                "type": "knn_vector",
                "dimension": 1024,
            },
            "source_bucket": {"type": "keyword"},
            "source_key": {"type": "keyword"},
            "doc_class": {"type": "keyword"},
            "extraction_method": {"type": "keyword"},
            "page_start": {"type": "integer"},
            "page_end": {"type": "integer"},
            "batch_index": {"type": "integer"},
            "total_pages": {"type": "integer"},
            "file_size_bytes": {"type": "long"},
            "schema_version": {"type": "keyword"},
            "has_citations": {"type": "boolean"},
            "has_tables": {"type": "boolean"},
            "indexed_at": {"type": "date"},
        }
    },
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    index_name: str
    documents_indexed: int
    documents_failed: int
    documents_skipped: int
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def build_opensearch_client(config: PdfPipelineConfig) -> OpenSearch:
    """Build an IAM/SigV4-authenticated OpenSearch Serverless client."""
    if not config.opensearch_host:
        raise ValueError(
            "OPENSEARCH_HOST is not set. "
            "Set it via environment variable or PdfPipelineConfig.opensearch_host"
        )

    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, config.aws_region, "aoss")

    return OpenSearch(
        hosts=[{"host": config.opensearch_host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=config.opensearch_verify_certs,
        connection_class=RequestsHttpConnection,
        pool_maxsize=10,
        # opensearch-py defaults to a 10s read timeout, which AOSS exceeds on the
        # first operations against a freshly-created NextGen index (warm-up).
        # Match the CSV client: longer timeout + retry timed-out requests.
        timeout=30,
        max_retries=3,
        retry_on_timeout=True,
    )


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


def ensure_index_exists(
    client: OpenSearch,
    config: PdfPipelineConfig,
) -> bool:
    """Create the pdf_legal_vecs index if it does not already exist.

    Returns True if the index was created, False if it already existed.
    """
    index = config.opensearch_index
    if client.indices.exists(index=index):
        logger.debug("Index '%s' already exists", index)
        return False

    client.indices.create(index=index, body=PDF_INDEX_MAPPING)
    logger.info("Created OpenSearch index: %s", index)
    return True


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------


def _section_to_doc(
    section: SectionRecord,
    embedding: list[float],
) -> dict:
    return {
        # No _id / _index here: AOSS NextGen vector collections auto-assign the
        # document _id and reject a custom one. section_id is kept as a normal
        # field for provenance/dedup.
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
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------


def _delete_existing_by_source(
    client: OpenSearch,
    index_name: str,
    source_keys: set[str],
) -> None:
    """Delete previously-indexed sections for these source documents (dedup).

    AOSS NextGen auto-assigns _id and forbids a custom one, so we cannot upsert by
    a stable section_id — and AOSS does not support _delete_by_query at all (it
    404s unconditionally, regardless of index or document state), so matching
    docs are found via search and removed with a bulk delete-by-_id instead —
    the same delete-before-index pattern the CSV pipeline uses. Non-fatal: a
    missing index or zero matches is fine.
    """
    if not source_keys:
        return
    try:
        resp = client.search(
            index=index_name,
            body={
                "query": {"terms": {"source_key": list(source_keys)}},
                "_source": False,
                "size": 10_000,
            },
        )
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            return
        actions = [
            {"_op_type": "delete", "_index": index_name, "_id": hit["_id"]}
            for hit in hits
        ]
        deleted, errors = os_bulk(
            client, actions, raise_on_error=False, raise_on_exception=False
        )
        if deleted:
            logger.info(
                "Deleted %d existing section(s) for %d source key(s)",
                deleted,
                len(source_keys),
            )
        if errors:
            logger.debug(
                "%d delete error(s) for source_keys=%s", len(errors), source_keys
            )
    except Exception:
        logger.debug(
            "delete failed for source_keys=%s (non-fatal)", source_keys, exc_info=True
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_sections(
    sections_with_embeddings: list[tuple[SectionRecord, list[float]]],
    config: PdfPipelineConfig,
    opensearch_client: OpenSearch | None = None,
    force: bool = False,
) -> IngestResult:
    """Bulk-index SectionRecord objects with their embeddings into pdf_legal_vecs.

    Args:
        sections_with_embeddings: List of (SectionRecord, embedding) tuples.
        config: PdfPipelineConfig with index name and batch size.
        opensearch_client: Reusable OpenSearch client (created if None).
        force: Retained for API compatibility. Ingest always replaces a source
               document's prior sections (delete-before-index), since AOSS NextGen
               forbids a custom _id and cannot upsert — so re-runs stay idempotent.

    Returns:
        IngestResult with counts and any errors.
    """
    client = opensearch_client or build_opensearch_client(config)
    index = config.opensearch_index
    errors: list[str] = []

    ensure_index_exists(client, config)

    if not sections_with_embeddings:
        logger.warning("ingest_sections called with empty list — nothing to do")
        return IngestResult(
            index_name=index,
            documents_indexed=0,
            documents_failed=0,
            documents_skipped=0,
        )

    # Dedup: replace any previously-indexed sections of the same source document
    # before re-indexing. AOSS NextGen forbids a custom _id (so we cannot upsert),
    # so we delete-before-index — keeping re-runs idempotent (no duplicate sections
    # pile up). `force` is retained for API compatibility; replacement always runs.
    source_keys = {s.metadata.source_key for s, _ in sections_with_embeddings}
    _delete_existing_by_source(client, index, source_keys)

    # Build bulk actions — no custom _id; AOSS NextGen assigns the document _id.
    actions: list[dict] = [
        {"_index": index, "_source": _section_to_doc(section, embedding)}
        for section, embedding in sections_with_embeddings
    ]
    skipped = 0

    # Batch bulk requests
    total_indexed = 0
    total_failed = 0

    for batch_start in range(0, len(actions), config.opensearch_bulk_batch_size):
        batch = actions[batch_start : batch_start + config.opensearch_bulk_batch_size]
        try:
            success_count, failed_items = os_bulk(
                client,
                batch,
                raise_on_error=False,
                stats_only=False,
            )
            total_indexed += success_count
            if failed_items:
                total_failed += len(failed_items)
                for item in failed_items:
                    err_info = item.get("index", {})
                    errors.append(
                        f"Failed to index {err_info.get('_id', '?')}: "
                        f"{err_info.get('error', {}).get('reason', 'unknown')}"
                    )
            logger.info(
                "Bulk batch %d/%d: %d indexed, %d failed",
                batch_start // config.opensearch_bulk_batch_size + 1,
                (len(actions) + config.opensearch_bulk_batch_size - 1)
                // config.opensearch_bulk_batch_size,
                success_count,
                len(failed_items) if failed_items else 0,
            )
        except Exception as exc:
            error_msg = f"Bulk request failed at batch offset {batch_start}: {exc}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)
            total_failed += len(batch)

    logger.info(
        "Ingest complete | index=%s | indexed=%d | failed=%d | skipped=%d",
        index,
        total_indexed,
        total_failed,
        skipped,
    )

    return IngestResult(
        index_name=index,
        documents_indexed=total_indexed,
        documents_failed=total_failed,
        documents_skipped=skipped,
        errors=errors,
    )
