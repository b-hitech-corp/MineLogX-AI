"""
bedrock_rag_agent.py — multi-model RAG agent on Bedrock + OpenSearch.

The production RAG chatbot: dual-index retrieval over Amazon OpenSearch Serverless
(the same stack as rag_chatbot.py) wrapped in a stateful agent (conversation memory,
query optimization, rerank, structured JSON contract — the structure of the legacy
rag_agent_EC2.py), with the generation LLM selectable at runtime among three models
via the Bedrock **Converse API**.

Selectable models (the user picks one per message, via the UI):
    "claude-sonnet-4.6"  -> us.anthropic.claude-sonnet-4-6   (default / current)
    "nova-pro"           -> us.amazon.nova-pro-v1:0
    "deepseek-v3.2"      -> deepseek.v3.2

What is shared vs. what varies:
    * SHARED external resources (identical for every model): the OpenSearch endpoint
      and both indexes, the Titan/Cohere query embedders, the system prompt, the
      context-assembly logic, the conversation history, and the response contract.
    * The SELECTED model drives the ENTIRE reasoning pipeline — both query
      optimization AND answer generation. This is deliberate: the feature exists to
      COMPARE the three models, so each one's end-to-end behaviour (including how it
      rewrites the search query, and therefore what it retrieves) is exactly the
      signal we want to measure — not something to hold constant.

Why Converse for all three (verified against AWS docs, 2026-07-03):
    * Claude Sonnet 4.6, Amazon Nova Pro v1, and DeepSeek V3.2 all support Converse
      and are available in-region in us-east-1.
    * DeepSeek V3.2 (not the older R1/distilled models, which lack Converse) has no
      cross-region inference profile, so the agent is pinned to us-east-1.
    * DeepSeek is a reasoning MoE model — Converse may return `reasoningContent`
      blocks; we extract `text` blocks only, so reasoning never leaks into the answer.

Prerequisite: Bedrock model access enabled in us-east-1 for all three models.

Environment
-----------
  AWS_REGION             default us-east-1 (must host all three models)
  OPENSEARCH_HOST        AOSS endpoint (no scheme)
  PDF_INDEX              default pdf_legal_vecs
  CSV_INDEX              default minelogx-telemetry-v1
  COHERE_EMBED_MODEL_ID  default cohere.embed-v4:0
  TITAN_EMBED_MODEL_ID   default amazon.titan-embed-text-v2:0
  RAG_CLAUDE_MODEL_ID / RAG_NOVA_MODEL_ID / RAG_DEEPSEEK_MODEL_ID  (override model ids)

Usage
-----
    from rag_agent.bedrock_rag_agent import BedrockRAGAgent

    agent = BedrockRAGAgent()
    print(agent.chat("What licence is needed to prospect in Zimbabwe?"))              # default: Claude
    print(agent.chat("And for underground operations?", model="nova-pro"))            # Nova Pro
    print(agent.chat("Summarise the fuel telemetry.", model="deepseek-v3.2"))         # DeepSeek V3.2
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

from rag_agent.prompts import (
    SYSTEM_PROMPT,
    build_answer_prompt,
    build_query_optimization_prompt,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (env-driven)
# ---------------------------------------------------------------------------

REGION = os.getenv("AWS_REGION", "us-east-1")
HOST = os.getenv("OPENSEARCH_HOST", "")
PDF_INDEX = os.getenv("PDF_INDEX", "pdf_legal_vecs")
CSV_INDEX = os.getenv("CSV_INDEX", "minelogx-telemetry-v1")
# Vectorized data-analysis results (computed KPIs / insights), client-scoped.
# "_vecs" convention (cf. csv_telemetry_vecs / pdf_legal_vecs); the CFN
# ApiFunction sets ANALYSIS_INDEX to this same value.
ANALYSIS_INDEX = os.getenv("ANALYSIS_INDEX", "analysis_vecs")
DIM = 1024
COHERE_MODEL = os.getenv("COHERE_EMBED_MODEL_ID", "cohere.embed-v4:0")
TITAN_MODEL = os.getenv("TITAN_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")


# ---------------------------------------------------------------------------
# Model registry — the ONLY model-dependent seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """A selectable generation model. Only what actually differs per model.

    `family` drives the few Converse quirks (e.g. Nova wants temperature XOR topP,
    which we honour by only ever sending temperature). `max_tokens` respects each
    model's output ceiling (DeepSeek V3.2 caps at 8K).
    """

    key: str
    model_id: str
    family: str  # "claude" | "nova" | "deepseek"
    max_tokens: int = 2048  # <= every model's output ceiling (DeepSeek: 8192)


MODELS: dict[str, ModelSpec] = {
    "claude-sonnet-4.6": ModelSpec(
        key="claude-sonnet-4.6",
        model_id=os.getenv("RAG_CLAUDE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        family="claude",
    ),
    "nova-pro": ModelSpec(
        key="nova-pro",
        model_id=os.getenv("RAG_NOVA_MODEL_ID", "us.amazon.nova-pro-v1:0"),
        family="nova",
    ),
    "deepseek-v3.2": ModelSpec(
        key="deepseek-v3.2",
        model_id=os.getenv("RAG_DEEPSEEK_MODEL_ID", "deepseek.v3.2"),
        family="deepseek",
        max_tokens=2048,  # DeepSeek V3.2 hard ceiling is 8192; stay well under it
    ),
}

DEFAULT_MODEL = "claude-sonnet-4.6"  # "the current implementation"


# ---------------------------------------------------------------------------
# Retrieval data structure
# ---------------------------------------------------------------------------


@dataclass
class Hit:
    index: str
    score: float
    text: str
    locator: str  # human-readable source reference for citations
    source_file: str = ""  # raw provenance key (telemetry client-scope check)
    findings: dict | None = None  # structured key_findings (analysis hits only)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class BedrockRAGAgent:
    """Stateful, multi-model RAG agent over the shared OpenSearch vector collection.

    Retrieval infrastructure (OpenSearch + Titan/Cohere embeddings), the system
    prompt, context assembly, and conversation history are shared resources. The
    generation model is selected per `chat()` call via `model` (a key of MODELS),
    and that selected model drives BOTH query optimization and answer generation —
    so each model's full end-to-end behaviour is what gets compared.

    Args:
        region:            AWS region hosting all three models (default us-east-1).
        opensearch_host:   AOSS endpoint (no scheme). Defaults to OPENSEARCH_HOST.
        pdf_index/csv_index: OpenSearch indexes to search (both, always).
        default_model:     Model key used when chat() gets no/unknown selection.
        top_k:             Candidates retrieved per index.
        top_n:             Documents kept after merge+rerank.
        max_history_turns: Conversation turns kept in memory (turn = user+assistant).
        temperature:       Generation temperature (sent alone — Nova-safe).
        bedrock_client/opensearch_client: injectable for testing.
    """

    def __init__(
        self,
        region: str = REGION,
        opensearch_host: str = HOST,
        pdf_index: str = PDF_INDEX,
        csv_index: str = CSV_INDEX,
        analysis_index: str = ANALYSIS_INDEX,
        default_model: str = DEFAULT_MODEL,
        top_k: int = 4,
        top_n: int = 4,
        analysis_top_k: int = 10,
        max_history_turns: int = 10,
        temperature: float = 0.2,
        bedrock_client: Any | None = None,
        opensearch_client: Any | None = None,
    ) -> None:
        self.region = region
        self.opensearch_host = opensearch_host
        self.pdf_index = pdf_index
        self.csv_index = csv_index
        self.analysis_index = analysis_index
        self.default_model = default_model if default_model in MODELS else DEFAULT_MODEL
        self.top_k = top_k
        self.top_n = top_n
        # Child hits collapse to fewer parents, so search more children than the
        # final top_n (per AWS hierarchical-chunking guidance).
        self.analysis_top_k = analysis_top_k
        self.max_history_turns = max_history_turns
        self.temperature = temperature

        self._bedrock = bedrock_client
        self._os = opensearch_client
        self._history: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Lazy clients
    # ------------------------------------------------------------------

    def _bedrock_rt(self):
        if self._bedrock is None:
            self._bedrock = boto3.client("bedrock-runtime", region_name=self.region)
        return self._bedrock

    def _client(self) -> OpenSearch:
        if self._os is None:
            if not self.opensearch_host:
                raise ValueError("OPENSEARCH_HOST is not set.")
            creds = boto3.Session().get_credentials()
            self._os = OpenSearch(
                hosts=[{"host": self.opensearch_host, "port": 443}],
                http_auth=AWSV4SignerAuth(creds, self.region, "aoss"),
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True,
            )
        return self._os

    # ------------------------------------------------------------------
    # Model resolution (the seam)
    # ------------------------------------------------------------------

    def _resolve_model(self, model: str | None) -> ModelSpec:
        """Map a UI selector to a ModelSpec, falling back to the default.

        Never hard-fails on a bad/missing selector — an unknown key falls back to
        the default model so a UI glitch can't break the chat.
        """
        if model and model in MODELS:
            return MODELS[model]
        if model:
            logger.warning(
                "[RAGAgent] Unknown model '%s' — falling back to '%s'",
                model,
                self.default_model,
            )
        return MODELS[self.default_model]

    # ------------------------------------------------------------------
    # Query embedders — MUST match the models/params used at ingest time
    # ------------------------------------------------------------------

    def _embed_query_titan(self, text: str) -> list[float]:
        """Titan Embed v2 — matches the PDF ingestor (1024-dim, normalized)."""
        resp = self._bedrock_rt().invoke_model(
            modelId=TITAN_MODEL,
            body=json.dumps({"inputText": text, "dimensions": DIM, "normalize": True}),
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(resp["body"].read())["embedding"]

    def _embed_query_cohere(self, text: str) -> list[float]:
        """Cohere embed-v4 — matches the CSV ingestor (int8 cast to float32)."""
        resp = self._bedrock_rt().invoke_model(
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

    # ------------------------------------------------------------------
    # Retrieval (shared infrastructure) — dual-index kNN + merge/rerank
    # ------------------------------------------------------------------

    def _knn(
        self,
        index: str,
        vector: list[float],
        k: int,
        filter_: dict | None = None,
    ) -> list[dict]:
        """kNN search over one index, with an optional metadata pre-filter.

        When ``filter_`` is given it is placed *inside* the knn clause, so
        OpenSearch applies it during the ANN traversal (efficient k-NN
        filtering) and still returns up to ``k`` matching hits — rather than
        post-filtering, which could return fewer than ``k``.
        """
        knn_clause: dict[str, Any] = {"vector": vector, "k": k}
        if filter_:
            knn_clause["filter"] = filter_
        body = {
            "size": k,
            "query": {"knn": {"text_embedding": knn_clause}},
        }
        return (
            self._client()
            .search(index=index, body=body)
            .get("hits", {})
            .get("hits", [])
        )

    def _fetch_parent(self, parent_id: str, client: str) -> dict | None:
        """Fetch the parent (section) doc for a matched child, by parent_id.

        Scoped to the client and chunk_level=parent as defense-in-depth. Returns
        the parent's `_source` (full section text + key_findings) or None.
        """
        body = {
            "size": 1,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"parent_id": parent_id}},
                        {"term": {"chunk_level": "parent"}},
                        {"term": {"client_id": client}},
                    ]
                }
            },
        }
        hits = (
            self._client()
            .search(index=self.analysis_index, body=body)
            .get("hits", {})
            .get("hits", [])
        )
        return hits[0].get("_source", {}) if hits else None

    def _retrieve(self, question: str, client: str | None = None) -> list[Hit]:
        """Embed with both models, kNN-search both indexes, merge and rerank.

        Client (tenant) isolation for telemetry:
          * The PDF/regulatory index is ALWAYS searched unfiltered — regulatory
            and legal documents are shared across all clients.
          * The CSV/telemetry index is client-scoped: it is only searched when a
            valid ``client`` is supplied, and only chunks whose ``source_file``
            begins with ``"{client}/"`` are eligible. When ``client`` is None
            (missing/invalid upstream) the telemetry index is skipped entirely —
            fail closed, answer from regulatory sources only.
        """
        hits: list[Hit] = []

        # --- PDF / regulatory: shared across all clients, never filtered ---
        try:
            for h in self._knn(
                self.pdf_index, self._embed_query_titan(question), self.top_k
            ):
                s = h.get("_source", {})
                hits.append(
                    Hit(
                        index=self.pdf_index,
                        score=h.get("_score", 0.0),
                        text=f"{s.get('title', '')}\n{s.get('body', '')}".strip(),
                        locator=f"{s.get('source_key', '?')} p.{s.get('page_start')}-{s.get('page_end')}",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RAGAgent] PDF index search failed: %s", exc)

        # --- CSV / telemetry: client-scoped; skipped when no valid client ---
        if client:
            # The trailing slash ANCHORS the match to a whole path segment so
            # client "C1" cannot match "C12/..." — both the OpenSearch `prefix`
            # filter and the Python `startswith` guard below use this exact
            # "{client}/" prefix, and both are whole-string prefix comparisons
            # (not char-membership), so they agree.
            client_prefix = f"{client}/"
            telemetry_filter = {
                "bool": {"filter": [{"prefix": {"source_file": client_prefix}}]}
            }
            try:
                for h in self._knn(
                    self.csv_index,
                    self._embed_query_cohere(question),
                    self.top_k,
                    filter_=telemetry_filter,
                ):
                    s = h.get("_source", {})
                    source_file = s.get("source_file", "")
                    # Defense-in-depth: even though the query pre-filters by
                    # client, re-check here so a filter regression or a doc
                    # missing source_file can never leak another tenant's data.
                    if not source_file.startswith(client_prefix):
                        logger.warning(
                            "[RAGAgent] Dropping telemetry hit outside client "
                            "scope '%s': source_file=%r",
                            client,
                            source_file,
                        )
                        continue
                    hits.append(
                        Hit(
                            index=self.csv_index,
                            score=h.get("_score", 0.0),
                            text=s.get("text", ""),
                            locator=f"{source_file or '?'} chunk {s.get('chunk_index')}",
                            source_file=source_file,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RAGAgent] CSV index search failed: %s", exc)

            # --- Analysis (computed KPIs/insights): client-scoped, hierarchical ---
            # kNN matches CHILD docs (one per finding) for precision, then we
            # collapse to the SECTION parent (full context) — the small-to-big
            # pattern. Only reached when a valid client is present.
            try:
                analysis_filter = {
                    "bool": {
                        "filter": [
                            {"term": {"client_id": client}},
                            {"term": {"chunk_level": "child"}},
                        ]
                    }
                }
                child_hits = self._knn(
                    self.analysis_index,
                    self._embed_query_cohere(question),
                    self.analysis_top_k,
                    filter_=analysis_filter,
                )
                # Collapse children → best score per parent_id (dedup).
                best_by_parent: dict[str, float] = {}
                for h in child_hits:
                    s = h.get("_source", {})
                    # Defense-in-depth: never trust a hit outside the client scope.
                    if s.get("client_id") != client:
                        logger.warning(
                            "[RAGAgent] Dropping analysis hit outside client "
                            "scope '%s': client_id=%r",
                            client,
                            s.get("client_id"),
                        )
                        continue
                    pid = s.get("parent_id")
                    if not pid:
                        continue
                    score = h.get("_score", 0.0)
                    if pid not in best_by_parent or score > best_by_parent[pid]:
                        best_by_parent[pid] = score

                for pid, score in best_by_parent.items():
                    parent = self._fetch_parent(pid, client)
                    if parent is None:
                        continue
                    section = parent.get("section", "?")
                    srcs = ", ".join(parent.get("source_files") or [])
                    hits.append(
                        Hit(
                            index=self.analysis_index,
                            score=score,
                            text=parent.get("text", ""),
                            locator=f"analysis:{section} ({srcs})",
                            findings=parent.get("key_findings"),
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RAGAgent] Analysis index search failed: %s", exc)
        else:
            logger.info(
                "[RAGAgent] No valid client supplied — skipping telemetry + analysis "
                "indexes (regulatory documents only)."
            )

        # Rerank: dedup by (index, locator), sort by score desc, keep top_n.
        seen: set[tuple[str, str]] = set()
        unique: list[Hit] = []
        for h in sorted(hits, key=lambda x: x.score, reverse=True):
            sig = (h.index, h.locator)
            if sig not in seen:
                seen.add(sig)
                unique.append(h)
        return unique[: self.top_n]

    # ------------------------------------------------------------------
    # Converse — the single generation path for all three models
    # ------------------------------------------------------------------

    def _converse_text(
        self,
        spec: ModelSpec,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> str:
        """One Converse call, uniform across Claude / Nova / DeepSeek.

        System prompt goes in the top-level `system` field (model-independent).
        Only `text` content blocks are returned — DeepSeek `reasoningContent`
        blocks are intentionally ignored so reasoning never reaches the user.
        Temperature is sent alone (no topP), which keeps Nova's temperature-XOR-topP
        rule satisfied.
        """
        resp = self._bedrock_rt().converse(
            modelId=spec.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            inferenceConfig={
                "maxTokens": max_tokens or spec.max_tokens,
                "temperature": self.temperature,
            },
        )
        content = resp["output"]["message"]["content"]
        return "".join(b.get("text", "") for b in content if "text" in b).strip()

    # ------------------------------------------------------------------
    # Query optimization — runs on the SELECTED model
    # ------------------------------------------------------------------

    def _optimize_query(self, user_message: str, spec: ModelSpec) -> str:
        """Rewrite the user message into a keyword-focused search query.

        Runs on the SELECTED model (the same one that will generate the answer), so
        the whole pipeline — including how the query is rewritten and therefore what
        gets retrieved — reflects that model's behaviour. This makes cross-model
        comparison meaningful: retrieval is allowed to vary per model, by design.
        """
        history_context = self._format_history()
        prompt = build_query_optimization_prompt(history_context, user_message)
        try:
            optimized = self._converse_text(
                spec,
                [{"role": "user", "content": [{"text": prompt}]}],
                max_tokens=200,
            )
            # Some models wrap reasoning in <think>...</think>; strip it defensively.
            optimized = re.sub(
                r"<think>.*?</think>", "", optimized, flags=re.DOTALL
            ).strip()
            return optimized or user_message
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RAGAgent] Query optimization failed (%s); using raw message", exc
            )
            return user_message

    # ------------------------------------------------------------------
    # Answer generation with the SELECTED model
    # ------------------------------------------------------------------

    @staticmethod
    def _context_block(index: int, hit: Hit) -> str:
        """One numbered context entry. For analysis hits, the structured
        key_findings are appended verbatim so the model answers with the exact
        computed numbers rather than paraphrasing them out of the prose."""
        block = f"[{index}] (source: {hit.locator})\n{hit.text[:1500]}"
        if hit.findings:
            block += "\nExact values (verbatim): " + json.dumps(
                hit.findings, ensure_ascii=False, default=str
            )
        return block

    def _generate_answer(
        self, user_message: str, hits: list[Hit], spec: ModelSpec
    ) -> str:
        if not hits:
            return "No relevant context was retrieved from either index."

        context = "\n\n".join(
            self._context_block(i + 1, h) for i, h in enumerate(hits)
        )
        # Prior turns as Converse messages, then the grounded current turn.
        messages = self._history_as_messages()
        messages.append(
            {
                "role": "user",
                "content": [{"text": build_answer_prompt(context, user_message)}],
            }
        )
        return self._converse_text(spec, messages)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(
        self,
        user_message: str,
        model: str | None = None,
        client: str | None = None,
    ) -> str:
        """Answer a user message with the selected model; return a JSON string.

        Args:
            user_message: the user's question.
            model: a MODELS key ("claude-sonnet-4.6" | "nova-pro" | "deepseek-v3.2").
                   Unknown/None falls back to the default model.
            client: the tenant selected in the UI. Scopes telemetry retrieval to
                    that client (regulatory docs are always shared). Must already
                    be validated by the caller; None/invalid → telemetry is
                    skipped (regulatory-only). Passed per call and never stored on
                    the instance, so a warm-container singleton cannot leak one
                    request's client into the next.

        The selected model drives both query optimization and generation. The
        response JSON contract is identical across models; the `model` block echoes
        which model actually answered so the UI can display it.
        """
        spec = self._resolve_model(model)
        logger.info(
            "[RAGAgent] chat model=%s client=%s: '%s'", spec.key, client, user_message
        )

        try:
            search_query = self._optimize_query(user_message, spec)
            hits = self._retrieve(search_query, client=client)

            t0 = time.perf_counter()
            answer = self._generate_answer(user_message, hits, spec)
            elapsed = time.perf_counter() - t0

            self._append_to_history(user_message, answer)

            documents = [
                {
                    "rank": i + 1,
                    "index": h.index,
                    "score": round(h.score, 6),
                    "locator": h.locator,
                    "text_preview": h.text[:300],
                }
                for i, h in enumerate(hits)
            ]
            response_dict = {
                "success": True,
                "query": {"original": user_message, "optimized": search_query},
                "answer": answer,
                "retrieval": {
                    # Telemetry (CSV) + analysis indexes are only searched for a valid client.
                    "indexes": [self.pdf_index]
                    + ([self.csv_index, self.analysis_index] if client else []),
                    "client": client,
                    "top_k": self.top_k,
                    "top_n": self.top_n,
                    "document_count": len(documents),
                    "documents": documents,
                },
                "model": {
                    "selected": spec.key,
                    "model_id": spec.model_id,
                    "temperature": self.temperature,
                    "max_tokens": spec.max_tokens,
                },
                "performance": {"generation_elapsed_s": round(elapsed, 3)},
                "conversation": {
                    "turn": len(self._history) // 2,
                    "history_turns": min(
                        len(self._history) // 2, self.max_history_turns
                    ),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception:
            logger.error("[RAGAgent] Pipeline failed.", exc_info=True)
            response_dict = {
                "success": False,
                "query": {"original": user_message, "optimized": ""},
                "answer": "An internal error occurred while processing your request.",
                "retrieval": {
                    "indexes": [self.pdf_index]
                    + ([self.csv_index, self.analysis_index] if client else []),
                    "client": client,
                    "top_k": self.top_k,
                    "top_n": self.top_n,
                    "document_count": 0,
                    "documents": [],
                },
                "model": {
                    "selected": spec.key,
                    "model_id": spec.model_id,
                    "temperature": self.temperature,
                    "max_tokens": spec.max_tokens,
                },
                "performance": {"generation_elapsed_s": 0.0},
                "conversation": {
                    "turn": len(self._history) // 2,
                    "history_turns": min(
                        len(self._history) // 2, self.max_history_turns
                    ),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return json.dumps(response_dict, ensure_ascii=False, indent=2)

    def ping(self, model: str | None = None) -> bool:
        """Reachability probe: a one-token Converse to confirm model access.

        Returns True if the model answered, False otherwise (e.g. access not
        enabled in this region). Useful for a startup check across MODELS.
        """
        spec = self._resolve_model(model)
        try:
            self._converse_text(
                spec,
                [{"role": "user", "content": [{"text": "ping"}]}],
                max_tokens=1,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RAGAgent] ping failed for %s (%s): %s", spec.key, spec.model_id, exc
            )
            return False

    def reset_history(self) -> None:
        """Clear the conversation history, starting a fresh session."""
        self._history = []

    @property
    def history(self) -> list[dict[str, str]]:
        """Read-only view of the current conversation history."""
        return list(self._history)

    # ------------------------------------------------------------------
    # History management (model-independent)
    # ------------------------------------------------------------------

    def _append_to_history(self, user_message: str, assistant_answer: str) -> None:
        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": assistant_answer})
        max_messages = self.max_history_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def _history_as_messages(self) -> list[dict]:
        """Convert stored history to Converse message blocks (last few turns)."""
        return [
            {"role": m["role"], "content": [{"text": m["content"]}]}
            for m in self._history[-6:]
        ]

    def _format_history(self) -> str:
        if not self._history:
            return ""
        return "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in self._history[-6:]
        )


# ---------------------------------------------------------------------------
# CLI — quick manual check (optional model selection)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = argv if argv is not None else sys.argv[1:]
    model = None
    if argv and argv[0] in MODELS:
        model = argv.pop(0)
    agent = BedrockRAGAgent()
    if argv:
        print(agent.chat(" ".join(argv), model=model))
        return 0
    print(
        f"Multi-model RAG agent — models: {list(MODELS)} (default {DEFAULT_MODEL}). Ctrl-D to exit."
    )
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q:
            print(agent.chat(q, model=model))


if __name__ == "__main__":
    raise SystemExit(main())
