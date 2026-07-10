"""
analysis_ingestor.py
====================
Vectorize data-analysis result chunks into the AOSS analysis index.

Mirrors csv_pipeline/tools/opensearch_ingestor.py (SigV4 AOSS client, Cohere
Embed v4, int8→float32, bulk indexing), but for the hierarchical parent-child
analysis docs produced by analysis_report_serializer.

Key differences from the CSV ingestor:
  * Two doc levels — only CHILD docs are embedded; PARENT docs carry the full
    section text + key_findings and are stored with no vector.
  * Idempotent client replacement — because AOSS VECTORSEARCH uses
    server-generated IDs (no upsert by _id), a client's prior docs are removed
    with delete_by_query {term: client_id} before re-indexing.

Public API
----------
    ensure_index_exists(client, index_name) -> bool
    replace_client(client_id, chunks, *, client=None, index_name=None) -> IngestResult
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
from opensearchpy.helpers import bulk as os_bulk

from data_analysis_agent.config.settings import settings
from data_analysis_agent.tools.analysis_report_serializer import AnalysisChunk

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = 2.0

# AOSS NextGen vector index mapping. No engine/method (NextGen auto-selects
# HNSW) and no shard/replica settings (AOSS manages them) — mirrors the CSV
# telemetry index so retrieval behaves identically.
_INDEX_BODY = {
    "settings": {"index.knn": True},
    "mappings": {
        "properties": {
            "text": {"type": "text", "analyzer": "standard"},
            "text_embedding": {"type": "knn_vector", "dimension": 1024},
            "chunk_level": {"type": "keyword"},
            "parent_id": {"type": "keyword"},
            "client_id": {"type": "keyword"},
            "section": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            # Structured findings — stored, not indexed (avoids mapping explosion).
            "key_findings": {"type": "object", "enabled": False},
            "kpi_names": {"type": "keyword"},
            "source_files": {"type": "keyword"},
            "report_processed_at": {"type": "date", "ignore_malformed": True},
            "embed_model_id": {"type": "keyword"},
            "content_signature": {"type": "keyword"},
            "signature_type": {"type": "keyword"},
            "pipeline_version": {"type": "keyword"},
        }
    },
}


@dataclass
class IngestResult:
    index_name: str
    client_id: str
    documents_indexed: int = 0
    documents_failed: int = 0
    documents_deleted: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def _build_aoss_client() -> OpenSearch:
    cfg = settings.opensearch
    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, cfg.aws_region, "aoss")
    return OpenSearch(
        hosts=[{"host": cfg.host, "port": cfg.port}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=cfg.verify_certs,
        connection_class=RequestsHttpConnection,
        timeout=30,
        max_retries=3,
        retry_on_timeout=True,
    )


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


def ensure_index_exists(client: OpenSearch, index_name: str) -> bool:
    """Create the analysis knn index if absent. Returns True if it was created."""
    if client.indices.exists(index=index_name):
        return False
    client.indices.create(index=index_name, body=_INDEX_BODY)
    logger.info("[analysis_ingestor] Created index '%s'", index_name)
    return True


# ---------------------------------------------------------------------------
# Embedding (Cohere Embed v4 — same contract as the CSV ingestor)
# ---------------------------------------------------------------------------


def _embed_texts(bedrock_rt, texts: list[str]) -> list[list[float]]:
    """Embed child texts with Cohere Embed v4 (int8 → float32)."""
    cfg = settings.opensearch
    body: dict = {
        "texts": texts,
        "input_type": settings.analysis_ingest.embed_input_type,
        "truncate": "END",
        "output_dimension": cfg.output_dimension,
    }
    if cfg.embedding_type == "int8":
        body["embedding_types"] = ["int8"]

    last_exc: Optional[Exception] = None
    result: dict = {}
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = bedrock_rt.invoke_model(
                modelId=cfg.embedding_model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(resp["body"].read())
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
    else:
        raise RuntimeError(f"Bedrock embed failed after {_RETRY_ATTEMPTS}: {last_exc}")

    raw = result.get("embeddings", {})
    if isinstance(raw, dict):
        vecs = raw.get(cfg.embedding_type, raw.get("float", []))
    else:
        vecs = raw
    return [[float(v) for v in vec] for vec in vecs]


# ---------------------------------------------------------------------------
# Replacement + indexing
# ---------------------------------------------------------------------------


def _delete_client_docs(client: OpenSearch, index_name: str, client_id: str) -> int:
    """Remove a client's prior analysis docs (idempotent re-ingest).

    Required because AOSS VECTORSEARCH assigns server-generated IDs, so we
    cannot overwrite by _id. Returns the deleted count (0 if index absent).
    """
    if not client.indices.exists(index=index_name):
        return 0
    resp = client.delete_by_query(
        index=index_name,
        body={"query": {"term": {"client_id": client_id}}},
        params={"conflicts": "proceed"},
    )
    deleted = int(resp.get("deleted", 0))
    logger.info(
        "[analysis_ingestor] Deleted %d prior doc(s) for client '%s'", deleted, client_id
    )
    return deleted


def _doc_source(chunk: AnalysisChunk) -> dict:
    doc = {
        "text": chunk.text,
        "chunk_level": chunk.chunk_level,
        "parent_id": chunk.parent_id,
        "client_id": chunk.client_id,
        "section": chunk.section,
        "doc_type": chunk.doc_type,
        "key_findings": chunk.key_findings,
        "kpi_names": chunk.kpi_names,
        "source_files": chunk.source_files,
        "report_processed_at": chunk.report_processed_at or None,
        "embed_model_id": chunk.embed_model_id,
        "content_signature": chunk.content_signature,
        "signature_type": chunk.signature_type,
        "pipeline_version": chunk.pipeline_version,
    }
    # Only child docs carry a vector (parents are fetched by parent_id, never kNN'd).
    if chunk.chunk_level == "child" and chunk.embedding:
        doc["text_embedding"] = chunk.embedding
    return doc


def replace_client(
    client_id: str,
    chunks: list[AnalysisChunk],
    *,
    client: OpenSearch = None,
    bedrock_rt: Any = None,
    index_name: Optional[str] = None,
) -> IngestResult:
    """Idempotently (re)index one client's analysis chunks.

    Embeds child docs (parents are stored without vectors), deletes the client's
    prior docs, then bulk-indexes the fresh set. Never raises on per-doc failure;
    counts are captured in the result.
    """
    idx = index_name or settings.opensearch.analysis_index_name
    result = IngestResult(index_name=idx, client_id=client_id)

    os_client = client or _build_aoss_client()
    brt = bedrock_rt or boto3.client(
        "bedrock-runtime", region_name=settings.opensearch.aws_region
    )

    try:
        ensure_index_exists(os_client, idx)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"index creation failed: {exc}")
        return result

    # Embed child docs in batches (parents are not embedded).
    children = [c for c in chunks if c.chunk_level == "child"]
    batch = settings.opensearch.bulk_batch_size
    try:
        for start in range(0, len(children), batch):
            group = children[start : start + batch]
            vectors = _embed_texts(brt, [c.text for c in group])
            if len(vectors) != len(group):
                result.errors.append(
                    f"embed batch {start // batch}: expected {len(group)}, got {len(vectors)}"
                )
                continue
            for c, vec in zip(group, vectors):
                c.embedding = vec
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"embedding failed: {exc}")
        return result

    # Delete prior docs for this client (idempotent replacement).
    try:
        result.documents_deleted = _delete_client_docs(os_client, idx, client_id)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"delete_by_query failed: {exc}")

    # Bulk index parents + embedded children.
    actions = [{"_index": idx, "_source": _doc_source(c)} for c in chunks]
    try:
        indexed, errors = os_bulk(
            os_client, actions, raise_on_error=False, raise_on_exception=False
        )
        result.documents_indexed = indexed
        result.documents_failed = len(errors)
        for err in errors[:5]:
            result.errors.append(f"index error: {err}")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"bulk index failed: {exc}")
        result.documents_failed = len(actions)

    logger.info(
        "[analysis_ingestor] client=%s: %d indexed, %d deleted, %d failed",
        client_id,
        result.documents_indexed,
        result.documents_deleted,
        result.documents_failed,
    )
    return result
