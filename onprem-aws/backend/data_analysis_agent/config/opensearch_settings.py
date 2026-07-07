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
