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
  NextGen collections auto-select HNSW configuration when engine/method are
  NOT specified. We explicitly specify nmslib/hnsw/cosine for deterministic
  behavior and future-proofing against NextGen defaults changing.

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
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
from opensearchpy.helpers import bulk as os_bulk

from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig
from pdf_pipeline.tools.pdf_normalizer import SectionRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index mapping
# ---------------------------------------------------------------------------

PDF_INDEX_MAPPING = {
    "settings": {
        "index.knn": True
    },
    "mappings": {
        "properties": {
            "section_id":        {"type": "keyword"},
            "title":             {"type": "text", "analyzer": "standard",
                                  "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
            "body":              {"type": "text", "analyzer": "standard"},
            "text_embedding":    {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosine",
                    "engine": "nmslib",
                    "parameters": {"ef_construction": 512, "m": 16},
                },
            },
            "source_bucket":     {"type": "keyword"},
            "source_key":        {"type": "keyword"},
            "doc_class":         {"type": "keyword"},
            "extraction_method": {"type": "keyword"},
            "page_start":        {"type": "integer"},
            "page_end":          {"type": "integer"},
            "batch_index":       {"type": "integer"},
            "total_pages":       {"type": "integer"},
            "file_size_bytes":   {"type": "long"},
            "schema_version":    {"type": "keyword"},
            "has_citations":     {"type": "boolean"},
            "has_tables":        {"type": "boolean"},
            "indexed_at":        {"type": "date"},
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
        "_index": "",          # set by the bulk helper
        "_id": section.section_id,
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

def _section_ids_already_indexed(
    section_ids: list[str],
    index_name: str,
    client: OpenSearch,
) -> set[str]:
    """Return the subset of section_ids that already exist in the index."""
    if not section_ids:
        return set()
    try:
        resp = client.mget(
            body={"ids": section_ids},
            index=index_name,
            _source=False,
        )
        return {
            doc["_id"]
            for doc in resp.get("docs", [])
            if doc.get("found", False)
        }
    except Exception:
        logger.debug("mget idempotency check failed — assuming nothing indexed", exc_info=True)
        return set()


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
        force: If True, overwrite existing documents. If False (default),
               skip documents whose section_id is already indexed.

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

    # Idempotency check (skipped when force=True)
    already_indexed: set[str] = set()
    if not force:
        all_ids = [s.section_id for s, _ in sections_with_embeddings]
        already_indexed = _section_ids_already_indexed(all_ids, index, client)
        if already_indexed:
            logger.info(
                "Skipping %d already-indexed sections (force=False)",
                len(already_indexed),
            )

    # Build bulk actions
    actions: list[dict] = []
    skipped = 0
    for section, embedding in sections_with_embeddings:
        if section.section_id in already_indexed:
            skipped += 1
            continue
        doc = _section_to_doc(section, embedding)
        doc.pop("_index")  # opensearch bulk helper adds this from index param
        actions.append({
            "_index": index,
            "_id": section.section_id,
            "_source": doc,
        })

    if not actions:
        logger.info("All sections already indexed — nothing to ingest")
        return IngestResult(
            index_name=index,
            documents_indexed=0,
            documents_failed=0,
            documents_skipped=skipped,
        )

    # Batch bulk requests
    total_indexed = 0
    total_failed = 0

    for batch_start in range(0, len(actions), config.opensearch_bulk_batch_size):
        batch = actions[batch_start: batch_start + config.opensearch_bulk_batch_size]
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
                (len(actions) + config.opensearch_bulk_batch_size - 1) // config.opensearch_bulk_batch_size,
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
        index, total_indexed, total_failed, skipped,
    )

    return IngestResult(
        index_name=index,
        documents_indexed=total_indexed,
        documents_failed=total_failed,
        documents_skipped=skipped,
        errors=errors,
    )
