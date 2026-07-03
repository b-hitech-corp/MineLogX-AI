"""
opensearch_ingestor — Stage 4 of the CSV Vectorization Pipeline.

Reads the JSONL chunks produced by Stage 3, generates float32 embeddings by
calling Cohere Embed V4 on Amazon Bedrock, and bulk-indexes documents into an
Amazon OpenSearch Serverless (AOSS) NextGen vector search collection.

Authentication is IAM / SigV4 (service='aoss') — no username / password.
The collection must be a vector-search collection with a data-access policy
that grants at least:
    aoss:CreateIndex, aoss:WriteDocument, aoss:ReadDocument
    on the target index resource.

Index layout
------------
Each OpenSearch document contains:
  chunk_id       — unique string identifier (not used as _id; AOSS NextGen
                   vector collections do not support custom document IDs)
  text           — natural-language chunk text for BM25 search
  text_embedding — float32[1024] knn_vector for ANN retrieval
  source_file    — original S3 CSV key
  folder         — dataset folder (e.g. "C1")
  chunk_index    — sequential position within the file
  row_range      — [start, end] approximate parquet row positions
  date_start     — ISO-8601 timestamp of first event in the chunk
  date_end       — ISO-8601 timestamp of last event in the chunk
  entity_values  — {col_name: [values]} entity snapshot
  schema_version — schema_descriptor version string
  column_list    — list of column names present in the chunk
  row_count      — number of rows in the chunk

AOSS NextGen index mapping
--------------------------
knn_vector engine and method are NOT specified — NextGen auto-selects the
optimal HNSW configuration. Shard and replica settings are omitted; AOSS
manages them automatically.

Public API
----------
    ingest_chunks(file_path, local_mode, index_name) -> IngestResult
    ensure_index_exists(client, index_name)          -> bool
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
from opensearchpy.helpers import bulk as os_bulk

from csv_pipeline.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_INPUT_TYPE_DOC = "search_document"
_EMBED_DIMENSIONS     = 1024

_RETRY_ATTEMPTS   = 3
_RETRY_BACKOFF_S  = 2.0   # seconds; multiplied by attempt number

# AOSS NextGen vector index mapping.
# No engine / method — NextGen chooses the optimal HNSW configuration.
# No number_of_shards / number_of_replicas — AOSS manages these.
_INDEX_BODY = {
    "settings": {
        "index.knn": True,
    },
    "mappings": {
        "properties": {
            "chunk_id":       {"type": "keyword"},
            "text":           {"type": "text", "analyzer": "standard"},
            "text_embedding": {
                "type":      "knn_vector",
                "dimension": _EMBED_DIMENSIONS,
            },
            "source_file":    {"type": "keyword"},
            "folder":         {"type": "keyword"},
            "chunk_index":    {"type": "integer"},
            "row_range":      {"type": "integer"},
            "date_start":     {"type": "date", "ignore_malformed": True},
            "date_end":       {"type": "date", "ignore_malformed": True},
            "entity_values":  {"type": "object", "enabled": False},
            "schema_version": {"type": "keyword"},
            "column_list":    {"type": "keyword"},
            "row_count":      {"type": "integer"},
        }
    },
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IngestResult:
    index_name:        str
    documents_indexed: int       = 0
    documents_failed:  int       = 0
    errors:            list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest_chunks(
    file_path: str,
    local_mode: bool = False,
    index_name: Optional[str] = None,
) -> IngestResult:
    """
    Stage 4 of the CSV Vectorization Pipeline.

    Reads the JSONL produced by Stage 3, embeds each chunk's text with
    Cohere Embed V4 on Bedrock, and bulk-indexes into the AOSS vector index.

    Parameters
    ----------
    file_path  : Original CSV S3 key (e.g. "C1/fuel_management_events.csv").
                 Used to locate the chunks JSONL and name log messages.
    local_mode : Read JSONL from sample_data/ (still writes to AOSS).
    index_name : Override the index from settings.opensearch.index_name.

    Returns
    -------
    IngestResult with counts and any per-batch errors.
    """
    cfg      = settings.opensearch
    idx_name = index_name or cfg.index_name
    result   = IngestResult(index_name=idx_name)

    # ── Step 1: Read JSONL ────────────────────────────────────────────────
    try:
        chunks = _read_jsonl(file_path, local_mode)
    except Exception as exc:
        result.errors.append(f"JSONL read failed: {exc}")
        logger.error("[opensearch_ingestor] JSONL read failed for '%s': %s", file_path, exc)
        return result

    if not chunks:
        result.errors.append("JSONL is empty — nothing to ingest")
        logger.warning("[opensearch_ingestor] Empty JSONL for '%s'", file_path)
        return result

    logger.info(
        "[opensearch_ingestor] '%s': %d chunks to ingest → index=%s",
        file_path, len(chunks), idx_name,
    )

    # ── Step 2: Build AOSS client (SigV4 only) ───────────────────────────
    try:
        os_client  = _build_aoss_client()
    except Exception as exc:
        result.errors.append(f"OpenSearch client build failed: {exc}")
        return result

    # ── Step 3: Ensure index exists ───────────────────────────────────────
    try:
        ensure_index_exists(os_client, idx_name)
    except Exception as exc:
        result.errors.append(f"index creation failed: {exc}")
        logger.error("[opensearch_ingestor] Index creation failed: %s", exc)
        return result

    # ── Step 4: Batch embed + index ───────────────────────────────────────
    bedrock_rt = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
    batch_size = cfg.bulk_batch_size

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start: batch_start + batch_size]
        batch_num = batch_start // batch_size

        texts = [c["text"] for c in batch]

        # Generate embeddings
        try:
            vectors = _embed_texts(bedrock_rt, texts)
        except Exception as exc:
            result.errors.append(f"embed batch {batch_num}: {exc}")
            result.documents_failed += len(batch)
            logger.warning(
                "[opensearch_ingestor] Embedding failed for batch %d of '%s': %s",
                batch_num, file_path, exc,
            )
            continue

        if len(vectors) != len(batch):
            result.errors.append(
                f"embed batch {batch_num}: expected {len(batch)} vectors, got {len(vectors)}"
            )
            result.documents_failed += len(batch)
            continue

        # Build and send bulk actions
        actions = _build_bulk_actions(batch, vectors, idx_name)
        try:
            indexed, failed_items = _bulk_index(os_client, actions)
            result.documents_indexed += indexed
            result.documents_failed  += len(failed_items)
            for item in failed_items[:5]:   # cap error list to avoid noise
                result.errors.append(f"index error: {item}")
            if failed_items:
                logger.warning(
                    "[opensearch_ingestor] %d doc(s) failed in batch %d of '%s'",
                    len(failed_items), batch_num, file_path,
                )
        except Exception as exc:
            result.errors.append(f"bulk index batch {batch_num}: {exc}")
            result.documents_failed += len(batch)
            logger.warning(
                "[opensearch_ingestor] Bulk index failed for batch %d of '%s': %s",
                batch_num, file_path, exc,
            )

    logger.info(
        "[opensearch_ingestor] '%s' complete: %d indexed, %d failed",
        file_path, result.documents_indexed, result.documents_failed,
    )
    return result


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

def ensure_index_exists(client: OpenSearch, index_name: str) -> bool:
    """
    Create the knn vector index if it does not already exist.

    Returns True if the index was created, False if it already existed.
    Raises on any unexpected error from the OpenSearch API.
    """
    if client.indices.exists(index=index_name):
        logger.debug("[opensearch_ingestor] Index '%s' already exists", index_name)
        return False

    client.indices.create(index=index_name, body=_INDEX_BODY)
    logger.info("[opensearch_ingestor] Created index '%s'", index_name)
    return True


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_texts(bedrock_rt, texts: list[str]) -> list[list[float]]:
    """
    Call Cohere Embed V4 on Bedrock to embed a batch of texts.

    Requests int8 embeddings (when cfg.embedding_type == "int8") and casts
    them to float32 for OpenSearch knn_vector storage.

    Retries up to _RETRY_ATTEMPTS times with linear back-off on transient
    failures (throttling, temporary unavailability).
    """
    cfg        = settings.opensearch
    embed_type = cfg.embedding_type   # "int8" or "float"

    body: dict = {
        "texts":      texts,
        "input_type": _EMBED_INPUT_TYPE_DOC,
        "truncate":   "END",
        # Cohere Embed v4 defaults to 1536 dims; pin to the index's dimension
        # (cfg.output_dimension, 1024) or the knn_vector mapping rejects the doc.
        "output_dimension": cfg.output_dimension,
    }
    if embed_type == "int8":
        body["embedding_types"] = ["int8"]

    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp   = bedrock_rt.invoke_model(
                modelId     = cfg.embedding_model_id,
                body        = json.dumps(body),
                contentType = "application/json",
                accept      = "application/json",
            )
            result = json.loads(resp["body"].read())
            break
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                wait = _RETRY_BACKOFF_S * (attempt + 1)
                logger.warning(
                    "[opensearch_ingestor] Bedrock embed attempt %d failed (%s); "
                    "retrying in %.1fs", attempt + 1, exc, wait,
                )
                time.sleep(wait)
    else:
        raise RuntimeError(
            f"Bedrock embed call failed after {_RETRY_ATTEMPTS} attempts: {last_exc}"
        )

    # Cohere response shape depends on whether embedding_types was requested:
    #   with  embedding_types → {"embeddings": {"int8": [[...]], "float": [[...]]}}
    #   without               → {"embeddings": [[...], ...]}
    raw = result.get("embeddings", {})
    if isinstance(raw, dict):
        vecs = raw.get(embed_type, raw.get("float", []))
    else:
        vecs = raw

    # Cast int8 integers to float32 for knn_vector storage.
    # All int8 values [-128, 127] are exactly representable as float32.
    return [[float(v) for v in vec] for vec in vecs]


# ---------------------------------------------------------------------------
# Bulk indexing
# ---------------------------------------------------------------------------

def _build_bulk_actions(
    chunks:     list[dict],
    vectors:    list[list[float]],
    index_name: str,
) -> list[dict]:
    """
    Build opensearch-py bulk action dicts.

    AOSS NextGen vector collections do not support custom _id values.
    chunk_id is stored in _source only, not as the document ID.
    """
    actions = []
    for chunk, vector in zip(chunks, vectors):
        meta       = chunk.get("metadata", {})
        date_range = meta.get("date_range") or {}

        doc = {
            "chunk_id":       chunk["chunk_id"],
            "text":           chunk["text"],
            "text_embedding": vector,
            "source_file":    meta.get("source_file", ""),
            "folder":         meta.get("folder", ""),
            "chunk_index":    meta.get("chunk_index", 0),
            "row_range":      meta.get("row_range", [0, 0]),
            "date_start":     date_range.get("start"),
            "date_end":       date_range.get("end"),
            "entity_values":  meta.get("entity_values", {}),
            "schema_version": meta.get("schema_version", "1.0"),
            "column_list":    meta.get("column_list", []),
            "row_count":      meta.get("row_count", 0),
        }
        # No "_id" key — AOSS NextGen vector collections assign IDs automatically
        actions.append({"_index": index_name, "_source": doc})

    return actions


def _bulk_index(
    client:  OpenSearch,
    actions: list[dict],
) -> tuple[int, list[dict]]:
    """
    Execute a bulk index operation.

    Returns (success_count, failed_items) where failed_items is a list of
    error detail dicts. Never raises — per-document failures are captured.
    """
    success, errors = os_bulk(
        client,
        actions,
        raise_on_error     = False,
        raise_on_exception = False,
    )
    failed: list[dict] = []
    for err in errors:
        op    = err.get("index", err.get("create", {}))
        failed.append({
            "id":    op.get("_id"),
            "error": op.get("error", str(err)),
        })
    return success, failed


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _build_aoss_client() -> OpenSearch:
    """
    Build an OpenSearch client configured for AOSS with IAM / SigV4 auth.

    Uses the default boto3 credential chain (instance role, env vars,
    ~/.aws/credentials, etc.).
    """
    cfg         = settings.opensearch
    credentials = boto3.Session().get_credentials()
    auth        = AWSV4SignerAuth(credentials, cfg.aws_region, "aoss")

    return OpenSearch(
        hosts              = [{"host": cfg.host, "port": cfg.port}],
        http_auth          = auth,
        use_ssl            = True,
        verify_certs       = cfg.verify_certs,
        connection_class   = RequestsHttpConnection,
        timeout            = 30,
        max_retries        = 3,
        retry_on_timeout   = True,
    )


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def _read_jsonl(file_path: str, local_mode: bool) -> list[dict]:
    """Read the JSONL produced by Stage 3 from S3 or local disk."""
    raw = _fetch_jsonl_bytes(file_path, local_mode)
    records = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _fetch_jsonl_bytes(file_path: str, local_mode: bool) -> bytes:
    p      = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    jsonl_name = f"{p.stem}.chunks.jsonl"

    if local_mode:
        local_path = (
            Path(settings.local_data_path)
            / "vectorization"
            / folder
            / "chunks"
            / jsonl_name
        )
        return local_path.read_bytes()

    s3_key = f"{settings.s3.prefix}vectorization/{folder}/chunks/{jsonl_name}"
    s3     = boto3.client("s3", region_name=settings.s3.region)
    obj    = s3.get_object(Bucket=settings.s3.bucket_name, Key=s3_key)
    return obj["Body"].read()
