"""
rag_agent.py
============
Single-turn and multi-turn RAG chat agent.

Retrieval flow:
    User message
      -> Optimize query (Qwen3 via Ollama)
      -> Embed query    (mxbai-embed-large via Ollama)
      -> Search S3 Vectors (kNN)
      -> Rerank results
      -> Generate answer (Qwen3 via Ollama) with retrieved context + conversation history
      -> Return JSON with answer + source documents

Dependencies:
    boto3 (S3 Vectors), requests (Ollama endpoints)

Usage:
    from rag_agent import RAGAgent

    agent = RAGAgent(
        vector_bucket_name="my-vector-bucket",
        index_name="mining-regs-index",
    )

    response = agent.chat("Is a dust concentration of 3 mg/m3 compliant?")
    print(response)   # JSON string

    # Continue the conversation — history is kept automatically
    response = agent.chat("What about for underground operations specifically?")
"""

from __future__ import annotations

import json
import re
import logging
import time
from datetime import datetime, timezone
from typing import Any

import boto3
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REGION = "us-east-2"

# Ollama endpoints
DEFAULT_LLM_ENDPOINT = "http://ec2-98-81-228-187.compute-1.amazonaws.com:11434"
DEFAULT_LLM_MODEL = "qwen3:8b"
DEFAULT_EMBEDDINGS_ENDPOINT = "http://ec2-3-208-23-94.compute-1.amazonaws.com:11434"
DEFAULT_EMBEDDINGS_MODEL = "mxbai-embed-large"

# Request timeouts (seconds)
DEFAULT_LLM_TIMEOUT = 120
DEFAULT_EMBEDDINGS_TIMEOUT = 30

# Retrieval
DEFAULT_TOP_K = 5  # candidates retrieved from S3 Vectors
DEFAULT_TOP_N = 3  # documents kept after reranking

# Conversation memory
DEFAULT_MAX_HISTORY = 10  # max turns kept in memory (each turn = user + assistant)

# Generation
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 1024  # approximate; passed as num_predict to Ollama


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a specialized regulatory compliance assistant for the mining industry. \
Your role is to verify whether measurements, values, and practices described by the user align \
with the applicable legislation, regulations, and technical standards found in the retrieved documents.

Verification guidelines:
- Compare every measurement or value mentioned by the user against the thresholds, \
limits, or specifications stated in the retrieved documents.
- State clearly whether each value COMPLIES, DOES NOT COMPLY, or CANNOT BE DETERMINED \
based on the available documents.
- Always cite the specific article, section, or clause from the source document that \
supports your determination.
- If a value is outside the allowed range, state the permitted range explicitly and \
quantify the deviation when possible.
- If the retrieved documents do not cover a particular measurement or topic, say so \
explicitly — do not infer or extrapolate from outside knowledge.
- If multiple regulations apply to the same measurement, evaluate compliance against each one separately.
- Use precise, technical language appropriate for regulatory and engineering contexts.
- Never soften or omit a non-compliance finding — accuracy and completeness are critical.

Response structure:
For each value or measurement raised by the user:
1. Restate the value as provided.
2. State the regulatory limit or requirement from the source document.
3. Issue a clear compliance verdict: COMPLIES / DOES NOT COMPLY / CANNOT BE DETERMINED.
4. Cite the source document, article, and section.
5. Add any relevant observations (e.g. conditions, exceptions, measurement method requirements)."""


# ---------------------------------------------------------------------------
# RAG Agent
# ---------------------------------------------------------------------------


class RAGAgent:
    """Single chat agent with retrieval-augmented generation from S3 Vectors.

    Uses Qwen3 (via Ollama) for query optimization and answer generation,
    and mxbai-embed-large (via Ollama) for query embedding. Both models
    must match the ones used during indexing in pdf_vectorizer.py.

    Manages its own conversation history. Each call to `chat()` appends the
    user message and the generated answer to the internal history, which is
    included in subsequent calls for multi-turn context.

    Args:
        vector_bucket_name: S3 Vectors bucket to query.
        index_name: Index name inside the bucket.
        region: AWS region for the S3 Vectors client.
        llm_endpoint: Base URL of the Ollama server hosting the chat LLM.
        llm_model: Ollama model name for query optimization and generation.
        embeddings_endpoint: Base URL of the Ollama server hosting the embedding model.
        embeddings_model: Ollama model name for query embedding.
        llm_timeout: Request timeout in seconds for LLM calls.
        embeddings_timeout: Request timeout in seconds for embedding calls.
        top_k: Number of nearest vectors to retrieve from S3 Vectors.
        top_n: Number of documents to keep after reranking (must be <= top_k).
        max_history_turns: Maximum conversation turns to keep in memory.
        temperature: Generation temperature for the final answer.
        max_tokens: Approximate maximum tokens in the generated answer (num_predict).
        s3vectors_client: Optional pre-built boto3 s3vectors client.
    """

    def __init__(
        self,
        vector_bucket_name: str,
        index_name: str,
        region: str = DEFAULT_REGION,
        llm_endpoint: str = DEFAULT_LLM_ENDPOINT,
        llm_model: str = DEFAULT_LLM_MODEL,
        embeddings_endpoint: str = DEFAULT_EMBEDDINGS_ENDPOINT,
        embeddings_model: str = DEFAULT_EMBEDDINGS_MODEL,
        llm_timeout: int = DEFAULT_LLM_TIMEOUT,
        embeddings_timeout: int = DEFAULT_EMBEDDINGS_TIMEOUT,
        top_k: int = DEFAULT_TOP_K,
        top_n: int = DEFAULT_TOP_N,
        max_history_turns: int = DEFAULT_MAX_HISTORY,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        s3vectors_client: Any | None = None,
    ) -> None:
        self.vector_bucket_name = vector_bucket_name
        self.index_name = index_name
        self.region = region
        self.llm_endpoint = llm_endpoint
        self.llm_model = llm_model
        self.embeddings_endpoint = embeddings_endpoint
        self.embeddings_model = embeddings_model
        self.llm_timeout = llm_timeout
        self.embeddings_timeout = embeddings_timeout
        self.top_k = top_k
        self.top_n = top_n
        self.max_history_turns = max_history_turns
        self.temperature = temperature
        self.max_tokens = max_tokens

        self._s3vectors = s3vectors_client or boto3.client(
            "s3vectors", region_name=region
        )

        # Conversation history stored as a list of {"role": ..., "content": ...} dicts
        self._history: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> str:
        """Process a user message and return a JSON string with the full response.

        The conversation history is updated automatically after each call.
        The response is built programmatically from a Python dictionary and
        serialized with json.dumps — the model never constructs the JSON itself.

        Args:
            user_message: The user's question or message.

        Returns:
            A JSON string with the following structure:

            {
                "success": bool,
                "query": {
                    "original":  str,   # the user's raw input
                    "optimized": str    # the search-optimized version sent to S3 Vectors
                },
                "answer": str,          # Qwen3's regulatory compliance response
                "retrieval": {
                    "vector_bucket": str,
                    "index":         str,
                    "top_k":         int,   # candidates retrieved
                    "top_n":         int,   # documents kept after reranking
                    "documents": [
                        {
                            "rank":             int,
                            "id":               str,
                            "similarity_score": float,
                            "source_key":       str,
                            "start_page":       int,
                            "end_page":         int,
                            "chunk_index":      int,
                            "sub_chunk_index":  int,
                            "chunk_strategy":   str,
                            "text_extractor":   str,
                            "text_preview":     str   # first 300 chars of the chunk text
                        }
                    ]
                },
                "model": {
                    "generation_model":   str,
                    "generation_endpoint": str,
                    "embedding_model":    str,
                    "embedding_endpoint": str,
                    "temperature":        float,
                    "max_tokens":         int
                },
                "performance": {
                    "llm_elapsed_s":        float,  # generation wall-clock time
                    "embedding_elapsed_s":  float   # embedding wall-clock time
                },
                "conversation": {
                    "turn":          int,   # current turn number (1-indexed)
                    "history_turns": int    # turns currently stored in memory
                },
                "timestamp": str            # UTC ISO-8601
            }
        """
        logger.info("[RAGAgent] Processing: '%s'", user_message)

        try:
            # Step 1: Optimize the user query for vector search
            search_query = self._optimize_query(user_message)
            logger.info("[RAGAgent] Optimized query: '%s'", search_query)

            # Step 2: Embed the optimized query
            embedding, embedding_elapsed = self._embed_query(search_query)

            # Step 3: Retrieve nearest vectors from S3 Vectors
            raw_docs = self._retrieve(embedding)
            logger.info("[RAGAgent] Retrieved %d raw documents", len(raw_docs))

            # Step 4: Rerank and keep top N
            documents = self._rerank(raw_docs)
            logger.info("[RAGAgent] Kept %d documents after reranking", len(documents))

            # Step 5: Generate answer — returns (answer_text, llm_elapsed_seconds)
            answer, llm_elapsed = self._generate_answer(user_message, documents)

            # Step 6: Persist turn to history before building the response
            self._append_to_history(user_message, answer)

            # ------------------------------------------------------------------
            # Build the response dictionary programmatically
            # ------------------------------------------------------------------

            serialized_docs = []
            for rank, doc in enumerate(documents, start=1):
                meta = doc.get("metadata", {})
                serialized_docs.append(
                    {
                        "rank": rank,
                        "id": doc.get("id", ""),
                        "similarity_score": round(doc.get("score", 0.0), 6),
                        "source_key": meta.get("source_key", ""),
                        "start_page": meta.get("start_page"),
                        "end_page": meta.get("end_page"),
                        "chunk_index": meta.get("chunk_index"),
                        "sub_chunk_index": meta.get("sub_chunk_index"),
                        "chunk_strategy": meta.get("chunk_strategy", ""),
                        "text_extractor": meta.get("text_extractor", ""),
                        "text_preview": doc.get("text", "")[:300],
                    }
                )

            response_dict = {
                "success": True,
                "query": {
                    "original": user_message,
                    "optimized": search_query,
                },
                "answer": answer,
                "retrieval": {
                    "vector_bucket": self.vector_bucket_name,
                    "index": self.index_name,
                    "top_k": self.top_k,
                    "top_n": self.top_n,
                    "document_count": len(serialized_docs),
                    "documents": serialized_docs,
                },
                "model": {
                    "generation_model": self.llm_model,
                    "generation_endpoint": self.llm_endpoint,
                    "embedding_model": self.embeddings_model,
                    "embedding_endpoint": self.embeddings_endpoint,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                },
                "performance": {
                    "llm_elapsed_s": round(llm_elapsed, 3),
                    "embedding_elapsed_s": round(embedding_elapsed, 3),
                },
                "conversation": {
                    "turn": len(self._history) // 2,
                    "history_turns": min(
                        len(self._history) // 2, self.max_history_turns
                    ),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as _exc:
            import traceback

            _tb = traceback.format_exc()
            logger.error("[RAGAgent] Pipeline failed.\n%s", _tb)

            response_dict = {
                "success": False,
                "query": {
                    "original": user_message,
                    "optimized": "",
                },
                "answer": f"An internal error occurred: {_exc}",
                "debug_traceback": _tb,
                "retrieval": {
                    "vector_bucket": self.vector_bucket_name,
                    "index": self.index_name,
                    "top_k": self.top_k,
                    "top_n": self.top_n,
                    "document_count": 0,
                    "documents": [],
                },
                "model": {
                    "generation_model": self.llm_model,
                    "generation_endpoint": self.llm_endpoint,
                    "embedding_model": self.embeddings_model,
                    "embedding_endpoint": self.embeddings_endpoint,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                },
                "performance": {
                    "llm_elapsed_s": 0.0,
                    "embedding_elapsed_s": 0.0,
                },
                "conversation": {
                    "turn": len(self._history) // 2,
                    "history_turns": min(
                        len(self._history) // 2, self.max_history_turns
                    ),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return json.dumps(response_dict, ensure_ascii=False, indent=2)

    def reset_history(self) -> None:
        """Clear the conversation history, starting a fresh session."""
        self._history = []
        logger.info("[RAGAgent] Conversation history cleared.")

    @property
    def history(self) -> list[dict[str, str]]:
        """Read-only view of the current conversation history."""
        return list(self._history)

    # ------------------------------------------------------------------
    # Step 1 — Query optimization
    # ------------------------------------------------------------------

    def _optimize_query(self, user_message: str) -> str:
        """Use Qwen3 to rewrite the user message into a search-optimized query.

        Calls /api/generate on the Ollama server with stream=False,
        matching the pattern used in the example code.
        """
        history_context = self._format_history_for_prompt()

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            "You are also a search query optimizer. "
            "Rewrite the user's message into a concise, keyword-focused search query "
            "for retrieving relevant documents from a vector database.\n\n"
            "<guidelines>\n"
            "1. Be concise — remove conversational filler.\n"
            "2. Preserve regulation codes, article numbers, and specific technical terms exactly.\n"
            "3. Include relevant synonyms when they add coverage.\n"
            "4. Use the conversation history only to resolve pronouns or ambiguous references.\n"
            "</guidelines>\n\n"
            f"<conversation_history>\n"
            f"{history_context or 'No previous conversation.'}\n"
            f"</conversation_history>\n\n"
            f"<user_message>\n{user_message}\n</user_message>\n\n"
            "Respond with only the optimized search query, nothing else:"
        )

        response = requests.post(
            f"{self.llm_endpoint}/api/generate",
            json={
                "model": self.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 200,
                },
            },
            timeout=self.llm_timeout,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()

        # Qwen3 sometimes wraps its reasoning in <think>...</think> before
        # the actual answer. Strip those blocks so only the final output remains.
        clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # Fall back to the original message if the model returned nothing
        return clean if clean else user_message

    # ------------------------------------------------------------------
    # Step 2 — Query embedding
    # ------------------------------------------------------------------

    def _embed_query(self, query: str) -> tuple[list[float], float]:
        """Embed a query string using mxbai-embed-large via the Ollama API.

        Returns:
            Tuple of (embedding_vector, elapsed_seconds).
        """
        start = time.time()

        response = requests.post(
            f"{self.embeddings_endpoint}/api/embeddings",
            json={
                "model": self.embeddings_model,
                "prompt": query,
            },
            timeout=self.embeddings_timeout,
        )
        response.raise_for_status()

        elapsed = time.time() - start
        embedding = response.json().get("embedding", [])

        if not embedding:
            raise RuntimeError("Ollama returned an empty embedding vector.")

        logger.debug(
            "[RAGAgent] Embedded query in %.2fs (%d dims)", elapsed, len(embedding)
        )
        return embedding, elapsed

    # ------------------------------------------------------------------
    # Step 3 — Retrieval from S3 Vectors
    # ------------------------------------------------------------------

    def _retrieve(self, embedding: list[float]) -> list[dict[str, Any]]:
        """Run a kNN search against the S3 Vectors index.

        Returns:
            List of documents with keys: id, score, distance, text, metadata.
        """
        response = self._s3vectors.query_vectors(
            vectorBucketName=self.vector_bucket_name,
            indexName=self.index_name,
            queryVector={"float32": embedding},
            topK=self.top_k,
            returnMetadata=True,
            returnDistance=True,
        )

        documents = []
        for match in response.get("vectors", []):
            metadata = match.get("metadata", {})
            documents.append(
                {
                    "id": match.get("key", ""),
                    "score": 1
                    - match.get("distance", 1.0),  # convert distance -> similarity
                    "distance": match.get("distance", 1.0),
                    "text": metadata.get(
                        "text", ""
                    ),  # actual chunk text stored by pdf_vectorizer
                    "metadata": metadata,
                }
            )

        return documents

    # ------------------------------------------------------------------
    # Step 4 — Reranking
    # ------------------------------------------------------------------

    def _rerank(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate and sort documents by similarity score, keeping top N.

        Args:
            documents: Raw list from _retrieve().

        Returns:
            Sorted, deduplicated list of at most top_n documents.
        """
        if not documents:
            return []

        # Deduplicate by vector key
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for doc in documents:
            if doc["id"] not in seen:
                seen.add(doc["id"])
                unique.append(doc)

        # Sort by descending similarity (higher score = more relevant)
        sorted_docs = sorted(unique, key=lambda d: d.get("score", 0.0), reverse=True)

        return sorted_docs[: self.top_n]

    # ------------------------------------------------------------------
    # Step 5 — Answer generation
    # ------------------------------------------------------------------

    def _generate_answer(
        self,
        user_message: str,
        documents: list[dict[str, Any]],
    ) -> tuple[str, float]:
        """Generate a grounded regulatory compliance answer using Qwen3.

        Builds a single prompt that includes the system instructions, the
        retrieved document context, the conversation history, and the user
        question. Calls /api/generate on the Ollama server with stream=False.

        Args:
            user_message: The original user question.
            documents: Reranked documents to use as context.

        Returns:
            Tuple of (answer_text, elapsed_seconds).
        """
        # Build context block from retrieved documents
        if documents:
            context_blocks = []
            for idx, doc in enumerate(documents, start=1):
                source = doc["metadata"].get("source_key", "unknown source")
                start_page = doc["metadata"].get("start_page")
                end_page = doc["metadata"].get("end_page")
                pages = (
                    f"pages {start_page + 1}–{end_page}"
                    if start_page is not None and end_page is not None
                    else "page unknown"
                )
                context_blocks.append(
                    f"[Document {idx} | {source} | {pages}]\n{doc['text']}"
                )
            context = "\n\n".join(context_blocks)
        else:
            context = "No relevant documents were found for this query."

        # Build conversation history block
        history_context = self._format_history_for_prompt()

        # Assemble the full prompt: system + history + context + question
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"<conversation_history>\n"
            f"{history_context or 'No previous conversation.'}\n"
            f"</conversation_history>\n\n"
            f"<context>\n{context}\n</context>\n\n"
            f"<question>\n{user_message}\n</question>\n\n"
            "Answer based only on the context above:"
        )

        start = time.time()

        response = requests.post(
            f"{self.llm_endpoint}/api/generate",
            json={
                "model": self.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                },
            },
            timeout=self.llm_timeout,
        )
        response.raise_for_status()

        elapsed = time.time() - start
        answer = response.json().get("response", "").strip()

        logger.debug("[RAGAgent] Answer generated in %.2fs", elapsed)
        return answer, elapsed

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _append_to_history(self, user_message: str, assistant_answer: str) -> None:
        """Append a completed turn to the conversation history.

        Trims the history to stay within max_history_turns. Each turn consists
        of one user message and one assistant message (2 entries).
        """
        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": assistant_answer})

        # Keep only the most recent N turns (N turns = N*2 messages)
        max_messages = self.max_history_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def _format_history_for_prompt(self) -> str:
        """Format conversation history as plain text for inclusion in prompts.

        Uses the last 3 turns (6 messages) — enough for pronoun and reference
        resolution without bloating the context window.
        """
        if not self._history:
            return ""
        lines = [
            f"{msg['role'].upper()}: {msg['content']}" for msg in self._history[-6:]
        ]
        return "\n".join(lines)
