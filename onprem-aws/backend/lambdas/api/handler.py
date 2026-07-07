"""
Frontend & API Layer Lambda — request router behind API Gateway.

Routes:
    GET  /health | /healthz        → health check (no agent call)
    POST /analyze                  → data_analysis_agent.FleetAgent
    POST /chat                     → rag_agent.BedrockRAGAgent

Both agents are instantiated lazily (module-level singletons) so they survive
warm invocations without re-initialising boto3 clients and OpenSearch connections.

Guardrail (GUARDRAIL_ID) is passed to the FleetAgent via its settings; the RAG
agent applies guardrails on the Bedrock side at the individual converse() call.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy agent singletons (initialised on first warm call to each route)
# ---------------------------------------------------------------------------

_fleet_agent = None
_rag_agent = None


def _get_fleet_agent():
    global _fleet_agent
    if _fleet_agent is None:
        from data_analysis_agent.agent.bedrock_orchestrator import FleetAgent

        _fleet_agent = FleetAgent()
    return _fleet_agent


def _get_rag_agent():
    global _rag_agent
    if _rag_agent is None:
        from rag_agent.bedrock_rag_agent import BedrockRAGAgent

        _rag_agent = BedrockRAGAgent()
    return _rag_agent


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _ok(body: dict | str, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
    }


def _err(message: str, status: int = 400) -> dict:
    return _ok({"error": message}, status)


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _handle_analyze(event: dict) -> dict:
    """POST /analyze — telemetry KPI / fleet analysis via FleetAgent."""
    body = _parse_body(event)
    question = (body.get("question") or body.get("message") or "").strip()
    if not question:
        return _err("'question' field is required")
    try:
        result = _get_fleet_agent().run(question)
        return _ok(
            {"success": True, "summary": result.summary, "charts": result.charts}
        )
    except Exception:
        logger.error("FleetAgent failed", exc_info=True)
        return _err("Analysis pipeline error", 502)


def _handle_chat(event: dict) -> dict:
    """POST /chat — compliance RAG Q&A via BedrockRAGAgent."""
    body = _parse_body(event)
    message = (body.get("message") or body.get("question") or "").strip()
    model = body.get(
        "model"
    )  # optional: "claude-sonnet-4.6" | "nova-pro" | "deepseek-v3.2"
    if not message:
        return _err("'message' field is required")
    try:
        response_json = _get_rag_agent().chat(message, model=model)
        return _ok(response_json)  # already a JSON string
    except Exception:
        logger.error("RAGAgent failed", exc_info=True)
        return _err("RAG pipeline error", 502)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict:  # noqa: ARG001
    method = (
        (event or {})
        .get("requestContext", {})
        .get("http", {})
        .get("method", "GET")
        .upper()
    )
    path = (event or {}).get("rawPath") or (event or {}).get("path") or "/"

    if path.rstrip("/") in ("", "/health", "/healthz"):
        return _ok(
            {
                "status": "ok",
                "service": "minelogx-api",
                "opensearch_host": os.environ.get("OPENSEARCH_HOST", ""),
                "guardrail_id": os.environ.get("GUARDRAIL_ID", ""),
            }
        )

    if path == "/analyze" and method == "POST":
        return _handle_analyze(event)

    if path == "/chat" and method == "POST":
        return _handle_chat(event)

    return _err(f"No route for {method} {path}", 404)
