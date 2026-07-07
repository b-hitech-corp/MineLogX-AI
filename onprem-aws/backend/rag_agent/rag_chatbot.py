"""
rag_chatbot.py — minimal dual-index RAG over the shared AOSS vector collection.

Verifies end-to-end retrieval + generation against BOTH vectorization indexes:
  - pdf_legal_vecs        PDF sections   (Titan Embed v2, 1024-dim, normalized)
  - minelogx-telemetry-v1 CSV chunks     (Cohere embed-v4, 1024-dim, int8)

Each question is embedded with BOTH models (each index must be queried with the
SAME model + parameters used at ingest time), kNN-searched against its index,
the hits merged, and Claude answers grounded in them with [n] source citations.

This module is SELF-CONTAINED — it deliberately does NOT import csv_pipeline or
pdf_pipeline (the RAG agent is its own deployment unit). The cost is that the
query-embedding request shapes below MUST stay in sync with the ingestors:
  * Cohere: csv_pipeline/tools/opensearch_ingestor.py::_embed_texts
            (here input_type="search_query"; docs used "search_document")
  * Titan : pdf_pipeline/tools/pdf_titan_embedder.py::embed_section

This is a verification seed for the real RAG agent, not the finished agent.

Environment
-----------
  OPENSEARCH_HOST        AOSS endpoint (no scheme), e.g. <id>.us-east-1.aoss.amazonaws.com
  AWS_REGION             default us-east-1
  PDF_INDEX              default pdf_legal_vecs
  CSV_INDEX              default minelogx-telemetry-v1
  RAG_CLAUDE_MODEL_ID    default us.anthropic.claude-sonnet-4-6
  COHERE_EMBED_MODEL_ID  default cohere.embed-v4:0
  TITAN_EMBED_MODEL_ID   default amazon.titan-embed-text-v2:0

Usage (from the repo root)
--------------------------
  python -m rag_agent.rag_chatbot "What licence is needed to prospect in Zimbabwe?"
  python -m rag_agent.rag_chatbot                # interactive REPL
  # or in a notebook:
  from rag_agent.rag_chatbot import answer, retrieve
  print(answer("..."))
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

# ---------------------------------------------------------------------------
# Config (env-driven)
# ---------------------------------------------------------------------------

REGION = os.getenv("AWS_REGION", "us-east-1")
HOST = os.getenv("OPENSEARCH_HOST", "")
PDF_INDEX = os.getenv("PDF_INDEX", "pdf_legal_vecs")
CSV_INDEX = os.getenv("CSV_INDEX", "minelogx-telemetry-v1")
DIM = 1024
COHERE_MODEL = os.getenv("COHERE_EMBED_MODEL_ID", "cohere.embed-v4:0")
TITAN_MODEL = os.getenv("TITAN_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
CLAUDE_MODEL = os.getenv("RAG_CLAUDE_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

_bedrock = None
_os = None


def _bedrock_rt():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def _client() -> OpenSearch:
    global _os
    if _os is None:
        if not HOST:
            raise SystemExit("OPENSEARCH_HOST is not set.")
        creds = boto3.Session().get_credentials()
        _os = OpenSearch(
            hosts=[{"host": HOST, "port": 443}],
            http_auth=AWSV4SignerAuth(creds, REGION, "aoss"),
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
            max_retries=3,
            retry_on_timeout=True,
        )
    return _os


# ---------------------------------------------------------------------------
# Query embedders — MUST match the models/params used at ingest time
# ---------------------------------------------------------------------------


def embed_query_titan(text: str) -> list[float]:
    """Titan Embed v2 — matches pdf_titan_embedder (1024-dim, normalized)."""
    resp = _bedrock_rt().invoke_model(
        modelId=TITAN_MODEL,
        body=json.dumps({"inputText": text, "dimensions": DIM, "normalize": True}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def embed_query_cohere(text: str) -> list[float]:
    """Cohere embed-v4 — matches the CSV ingestor, but input_type='search_query'.

    Docs were stored as int8 cast to float32, so the query MUST also be int8 cast
    to float32 — a float query against int8-scaled doc vectors would not match.
    """
    resp = _bedrock_rt().invoke_model(
        modelId=COHERE_MODEL,
        body=json.dumps(
            {
                "texts": [text],
                "input_type": "search_query",
                "truncate": "END",
                "output_dimension": DIM,
                "embedding_types": ["int8"],
            }
        ),
        contentType="application/json",
        accept="application/json",
    )
    out = json.loads(resp["body"].read()).get("embeddings", {})
    vec = out["int8"][0] if isinstance(out, dict) else out[0]
    return [float(v) for v in vec]


# ---------------------------------------------------------------------------
# kNN search + retrieval
# ---------------------------------------------------------------------------


def knn(index: str, vector: list[float], k: int = 4) -> list[dict]:
    body = {"size": k, "query": {"knn": {"text_embedding": {"vector": vector, "k": k}}}}
    return _client().search(index=index, body=body).get("hits", {}).get("hits", [])


@dataclass
class Hit:
    index: str
    score: float
    text: str
    locator: str  # human-readable source reference for citations


def retrieve(question: str, k: int = 4) -> list[Hit]:
    """Embed the question with both models, kNN-search both indexes, merge by score."""
    hits: list[Hit] = []

    # PDF index — Titan
    try:
        for h in knn(PDF_INDEX, embed_query_titan(question), k):
            s = h.get("_source", {})
            hits.append(
                Hit(
                    index=PDF_INDEX,
                    score=h.get("_score", 0.0),
                    text=f"{s.get('title', '')}\n{s.get('body', '')}".strip(),
                    locator=f"{s.get('source_key', '?')} p.{s.get('page_start')}-{s.get('page_end')}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[{PDF_INDEX} search error] {exc}", file=sys.stderr)

    # CSV index — Cohere
    try:
        for h in knn(CSV_INDEX, embed_query_cohere(question), k):
            s = h.get("_source", {})
            hits.append(
                Hit(
                    index=CSV_INDEX,
                    score=h.get("_score", 0.0),
                    text=s.get("text", ""),
                    locator=f"{s.get('source_file', '?')} chunk {s.get('chunk_index')}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[{CSV_INDEX} search error] {exc}", file=sys.stderr)

    return sorted(hits, key=lambda x: x.score, reverse=True)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def answer(question: str, k: int = 4) -> str:
    """Retrieve from both indexes and have Claude answer grounded in the hits."""
    hits = retrieve(question, k)
    if not hits:
        return "No relevant context was retrieved from either index."

    context = "\n\n".join(
        f"[{i + 1}] (source: {h.locator})\n{h.text[:1500]}" for i, h in enumerate(hits)
    )
    prompt = (
        "You are a mining-domain assistant. Answer the question using ONLY the "
        "context below, and cite the sources you use as [n]. If the context does "
        "not contain the answer, say so plainly.\n\n"
        f"Context:\n{context}\n\nQuestion: {question}"
    )
    resp = _bedrock_rt().converse(
        modelId=CLAUDE_MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
    )
    text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
    sources = "\n".join(
        f"  [{i + 1}] {h.index}  score={h.score:.3f}  {h.locator}"
        for i, h in enumerate(hits)
    )
    return f"{text}\n\nSources:\n{sources}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        print(answer(" ".join(argv)))
        return 0
    print(f"Dual-index RAG chatbot — PDF={PDF_INDEX}, CSV={CSV_INDEX}. Ctrl-D to exit.")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q:
            print(answer(q))


if __name__ == "__main__":
    raise SystemExit(main())
