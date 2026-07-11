"""
OpenSearch Serverless (AOSS) configuration for the CSV Vectorization Pipeline.

Targets Amazon OpenSearch Serverless NextGen vector search collections.
Authentication is IAM / SigV4 only — no username / password.

Required environment variables
-------------------------------
OPENSEARCH_HOST   — AOSS collection endpoint WITHOUT scheme or trailing slash
                    e.g. "abc123def456.us-east-1.aoss.amazonaws.com"

Optional environment variables
-------------------------------
OPENSEARCH_INDEX         — default "minelogx-telemetry-v1"
OPENSEARCH_VERIFY_CERTS  — "true" / "false", default "true"
AWS_REGION               — SigV4 signing region, default "us-east-1"
BEDROCK_EMBED_MODEL_ID   — override embedding model, default "cohere.embed-v4:0"
OPENSEARCH_BATCH_SIZE    — documents per bulk request, default 50
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class OpenSearchConfig:
    # AOSS collection endpoint (no https://, no trailing slash)
    host: str = field(default_factory=lambda: os.getenv("OPENSEARCH_HOST", ""))
    port: int = 443

    # TLS — always True for AOSS; set OPENSEARCH_VERIFY_CERTS=false only in dev tunnels
    verify_certs: bool = field(
        default_factory=lambda: (
            os.getenv("OPENSEARCH_VERIFY_CERTS", "true").lower() == "true"
        )
    )

    # AWS region used for both SigV4 signing and Bedrock embedding calls
    aws_region: str = field(
        default_factory=lambda: os.getenv("AWS_REGION", "us-east-1")
    )

    # OpenSearch index
    index_name: str = field(
        default_factory=lambda: os.getenv("OPENSEARCH_INDEX", "minelogx-telemetry-v1")
    )

    # Dedicated index for the vectorized data-analysis results (KPIs / insights).
    # Separate from the raw-telemetry index; queried by the RAG agent (ANALYSIS_INDEX).
    # Name follows the collection's "_vecs" convention (csv_telemetry_vecs /
    # pdf_legal_vecs). The code default equals the CFN ApiFunction env value, so
    # the ingestor (Fabric) and the RAG agent (Lambda) agree with or without env.
    analysis_index_name: str = field(
        default_factory=lambda: os.getenv("ANALYSIS_INDEX", "analysis_vecs")
    )

    # Embedding — must match the values used at query time in the RAG agent
    embedding_model_id: str = field(
        default_factory=lambda: os.getenv("BEDROCK_EMBED_MODEL_ID", "cohere.embed-v4:0")
    )
    output_dimension: int = 1024
    embedding_type: str = "int8"  # int8 → ~83% vector storage vs float32

    # Ingestion
    bulk_batch_size: int = field(
        default_factory=lambda: int(os.getenv("OPENSEARCH_BATCH_SIZE", "50"))
    )


@dataclass
class AnalysisIngestConfig:
    """Settings for vectorizing the data-analysis results into OpenSearch.

    The ledger is an append-only JSONL control log in S3 recording which source
    files were processed (path, ETag, model, timestamp) — the authority for
    "what is already indexed". `pipeline_version` is stamped on every doc and
    ledger record; bumping it forces a global re-ingest when the chunking or
    rendering logic changes.
    """

    # Bumped when the serializer / chunking logic changes → forces re-ingest.
    pipeline_version: str = field(
        default_factory=lambda: os.getenv("ANALYSIS_PIPELINE_VERSION", "1")
    )

    # S3 location of the ledger (control log). Defaults to the telemetry bucket
    # under the CLAUDE.md logs/ convention.
    ledger_bucket: str = field(
        default_factory=lambda: os.getenv(
            "ANALYSIS_LEDGER_BUCKET",
            os.getenv("FLEET_S3_BUCKET", "bhitech-minelogx-poc-telemetry-data"),
        )
    )
    ledger_key: str = field(
        default_factory=lambda: os.getenv(
            "ANALYSIS_LEDGER_KEY", "logs/analysis-ingest/ledger.jsonl"
        )
    )

    # Cohere document input_type at ingest (query side uses "search_query").
    embed_input_type: str = "search_document"
