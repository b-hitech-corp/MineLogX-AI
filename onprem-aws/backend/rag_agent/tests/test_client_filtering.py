"""
test_client_filtering.py
========================
Unit tests for RAG client (tenant) isolation of telemetry.

Covers the security-critical retrieval seam in bedrock_rag_agent.py:
  - the CSV/telemetry index is filtered by client; the PDF/regulatory index is not
  - a missing/invalid client fails closed (telemetry index skipped entirely)
  - the defense-in-depth post-filter drops any telemetry hit outside client scope
  - client "C1" is anchored and does not match "C12/..."
  - the chat() client argument is per-call (no leak between calls on one instance)

Plus the chat request contract in lambdas/api/handler.py (_handle_chat):
  - reads the new `query` field (and legacy `message`), validates `client`

All OpenSearch and Bedrock calls are mocked — no live AWS.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Repo package root (onprem-aws/backend) on sys.path — mirrors pdf_pipeline tests.
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from rag_agent.bedrock_rag_agent import BedrockRAGAgent, Hit  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(search_impl):
    """Build an agent with a mocked OpenSearch client and stubbed embedders.

    search_impl(index, body) -> raw OpenSearch response dict.
    """
    os_client = MagicMock()
    os_client.search.side_effect = lambda index, body: search_impl(index, body)

    agent = BedrockRAGAgent(
        opensearch_host="test.aoss.example.com",
        pdf_index="pdf_legal_vecs",
        csv_index="minelogx-telemetry-v1",
        bedrock_client=MagicMock(),
        opensearch_client=os_client,
    )
    # Skip real Bedrock embedding calls.
    agent._embed_query_titan = lambda text: [0.0] * 1024
    agent._embed_query_cohere = lambda text: [0.0] * 1024
    return agent, os_client


def _hit(source: dict, score: float = 1.0) -> dict:
    return {"_score": score, "_source": source}


def _empty(_index, _body):
    return {"hits": {"hits": []}}


# ---------------------------------------------------------------------------
# Retrieval filtering
# ---------------------------------------------------------------------------


class TestRetrievalClientFilter:
    def test_csv_query_is_filtered_pdf_is_not(self):
        """The CSV index query carries the client prefix filter; PDF does not."""
        calls: dict[str, dict] = {}

        def search_impl(index, body):
            calls[index] = body
            return {"hits": {"hits": []}}

        agent, _ = _make_agent(search_impl)
        agent._retrieve("some question", client="C1")

        pdf_knn = calls["pdf_legal_vecs"]["query"]["knn"]["text_embedding"]
        csv_knn = calls["minelogx-telemetry-v1"]["query"]["knn"]["text_embedding"]

        assert "filter" not in pdf_knn  # regulatory index never filtered
        assert csv_knn["filter"] == {
            "bool": {"filter": [{"prefix": {"source_file": "C1/"}}]}
        }

    def test_fail_closed_when_no_client_skips_csv(self):
        """No valid client → telemetry index is never queried."""
        searched: list[str] = []

        def search_impl(index, body):
            searched.append(index)
            return {"hits": {"hits": []}}

        agent, _ = _make_agent(search_impl)
        agent._retrieve("some question", client=None)

        assert "pdf_legal_vecs" in searched  # regulatory still searched
        assert "minelogx-telemetry-v1" not in searched  # telemetry skipped

    def test_post_filter_drops_foreign_client_hit(self):
        """Even if the query filter regresses and returns C2 data for a C1
        request, the post-retrieval assertion drops it."""

        def search_impl(index, body):
            if index == "minelogx-telemetry-v1":
                # Simulate a filter regression: a C2 doc leaks through.
                return {
                    "hits": {
                        "hits": [
                            _hit({"source_file": "C2/fuel.csv", "text": "secret C2"}),
                            _hit({"source_file": "C1/fuel.csv", "text": "ok C1"}),
                        ]
                    }
                }
            return {"hits": {"hits": []}}

        agent, _ = _make_agent(search_impl)
        hits = agent._retrieve("q", client="C1")

        telemetry = [h for h in hits if h.index == "minelogx-telemetry-v1"]
        assert all(h.source_file.startswith("C1/") for h in telemetry)
        assert not any("C2" in h.source_file for h in telemetry)
        assert any(h.source_file == "C1/fuel.csv" for h in telemetry)

    def test_anchoring_c1_does_not_match_c12(self):
        """Client 'C1' must not match a 'C12/...' document (segment-anchored)."""

        def search_impl(index, body):
            if index == "minelogx-telemetry-v1":
                return {
                    "hits": {"hits": [_hit({"source_file": "C12/fuel.csv", "text": "x"})]}
                }
            return {"hits": {"hits": []}}

        agent, _ = _make_agent(search_impl)
        hits = agent._retrieve("q", client="C1")

        assert not any(h.index == "minelogx-telemetry-v1" for h in hits)

    def test_client_is_per_call_not_stored(self):
        """Two calls with different clients filter independently on one agent."""
        calls: list[tuple[str, dict]] = []

        def search_impl(index, body):
            calls.append((index, body))
            return {"hits": {"hits": []}}

        agent, _ = _make_agent(search_impl)
        agent._retrieve("q", client="C1")
        agent._retrieve("q", client="C2")

        csv_bodies = [
            b["query"]["knn"]["text_embedding"]["filter"]
            for idx, b in calls
            if idx == "minelogx-telemetry-v1"
        ]
        assert csv_bodies[0] == {"bool": {"filter": [{"prefix": {"source_file": "C1/"}}]}}
        assert csv_bodies[1] == {"bool": {"filter": [{"prefix": {"source_file": "C2/"}}]}}


# ---------------------------------------------------------------------------
# Chat request contract (handler)
# ---------------------------------------------------------------------------


class TestChatHandlerContract:
    def _import_handler(self):
        # handler pulls in pandas etc.; skip cleanly if deps are absent locally.
        pytest.importorskip("pandas")
        from lambdas.api import handler

        return handler

    def _event(self, body: dict) -> dict:
        import json

        return {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/chat",
            "body": json.dumps(body),
        }

    def test_reads_query_and_valid_client(self):
        handler = self._import_handler()
        agent = MagicMock()
        agent.chat.return_value = "{}"
        with patch.object(handler, "_get_rag_agent", return_value=agent):
            handler._handle_chat(self._event({"query": "hello", "client": "C1"}))
        _, kwargs = agent.chat.call_args
        assert agent.chat.call_args[0][0] == "hello"
        assert kwargs["client"] == "C1"

    def test_invalid_client_passed_as_none(self):
        handler = self._import_handler()
        agent = MagicMock()
        agent.chat.return_value = "{}"
        with patch.object(handler, "_get_rag_agent", return_value=agent):
            handler._handle_chat(self._event({"query": "hi", "client": "C1/../C2"}))
        assert agent.chat.call_args.kwargs["client"] is None

    def test_missing_query_is_rejected(self):
        handler = self._import_handler()
        agent = MagicMock()
        with patch.object(handler, "_get_rag_agent", return_value=agent):
            resp = handler._handle_chat(self._event({"client": "C1"}))
        assert resp["statusCode"] == 400
        agent.chat.assert_not_called()
