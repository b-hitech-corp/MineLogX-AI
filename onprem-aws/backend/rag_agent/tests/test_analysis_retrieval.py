"""
test_analysis_retrieval.py
==========================
Unit tests for the RAG agent's retrieval from the analysis index (no live AWS):

  - kNN matches CHILD docs, filtered to the client;
  - a foreign-client child is dropped by the defense-in-depth post-filter;
  - multiple children of one section COLLAPSE to a single parent (small-to-big);
  - the parent's key_findings is surfaced into the generation context;
  - the analysis index is NOT searched when no valid client is supplied.

OpenSearch is mocked; embedders are stubbed so nothing hits Bedrock.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock


sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from rag_agent.bedrock_rag_agent import BedrockRAGAgent, Hit  # noqa: E402

ANALYSIS = "analysis_test"


def _make_agent(search_impl):
    os_client = MagicMock()
    os_client.search.side_effect = lambda index, body: search_impl(index, body)
    # _knn issues a count() to bound its ANN reach; a small value makes the
    # filtered retry-loop resolve in a single round for these fixed-result mocks.
    os_client.count.return_value = {"count": 2}
    agent = BedrockRAGAgent(
        opensearch_host="test.aoss.example.com",
        pdf_index="pdf_legal_vecs",
        csv_index="csv_telemetry_vecs",
        analysis_index=ANALYSIS,
        bedrock_client=MagicMock(),
        opensearch_client=os_client,
    )
    agent._embed_query_titan = lambda text: [0.0] * 1024
    agent._embed_query_cohere = lambda text: [0.0] * 1024
    return agent, os_client


# Child hits: two C1 children of the same parent + one foreign C2 child.
# Distinct _id on each — _knn dedups paged results by _id.
_CHILDREN = [
    {
        "_id": "c1a",
        "_score": 0.9,
        "_source": {
            "client_id": "C1",
            "parent_id": "C1:fuel",
            "chunk_level": "child",
            "text": "c1-a",
        },
    },
    {
        "_id": "c1b",
        "_score": 0.8,
        "_source": {
            "client_id": "C1",
            "parent_id": "C1:fuel",
            "chunk_level": "child",
            "text": "c1-b",
        },
    },
    {
        "_id": "c2a",
        "_score": 0.95,
        "_source": {
            "client_id": "C2",
            "parent_id": "C2:fuel",
            "chunk_level": "child",
            "text": "c2-secret",
        },
    },
]

_PARENTS = {
    "C1:fuel": {
        "_source": {
            "client_id": "C1",
            "parent_id": "C1:fuel",
            "chunk_level": "parent",
            "section": "fuel",
            "text": "Client C1 — fuel section.",
            "key_findings": {
                "kpis": [
                    {"name": "fuel_consumption_rate", "value": 124.006, "unit": "L/hr"}
                ]
            },
            "source_files": ["C1/fuel_management_events.csv"],
        }
    }
}


def _analysis_search_impl(index, body):
    """Serve child kNN and parent-fetch for the analysis index; empty elsewhere.

    Both queries are now bool queries (the filtered child kNN post-filters
    inside a bool; the parent fetch is a pure bool filter), so we distinguish
    them by whether a knn clause is present in bool.must.
    """
    if index != ANALYSIS:
        return {"hits": {"hits": []}}
    bool_q = body.get("query", {}).get("bool", {})
    must = bool_q.get("must", [])
    # Child kNN: knn clause inside bool.must → return the child hits.
    if any("knn" in m for m in must):
        return {"hits": {"hits": _CHILDREN}}
    # Parent fetch: pure bool filter carrying a parent_id term.
    for f in bool_q.get("filter", []):
        if "parent_id" in f.get("term", {}):
            parent = _PARENTS.get(f["term"]["parent_id"])
            return {"hits": {"hits": [parent] if parent else []}}
    return {"hits": {"hits": []}}


class TestAnalysisRetrieval:
    def test_children_collapse_to_one_parent_and_drop_foreign_client(self):
        agent, _ = _make_agent(_analysis_search_impl)
        hits = agent._retrieve("fuel efficiency", client="C1")

        analysis_hits = [h for h in hits if h.index == ANALYSIS]
        # Two C1 children of one parent collapse to a single parent hit.
        assert len(analysis_hits) == 1
        # The foreign C2 content never surfaces.
        assert all("c2-secret" not in (h.text or "") for h in hits)
        assert all("C2" not in (h.locator or "") for h in analysis_hits)

        parent_hit = analysis_hits[0]
        assert parent_hit.text == "Client C1 — fuel section."
        assert parent_hit.findings == {
            "kpis": [
                {"name": "fuel_consumption_rate", "value": 124.006, "unit": "L/hr"}
            ]
        }
        assert "analysis:fuel" in parent_hit.locator

    def test_parent_findings_reach_generation_context(self):
        hit = Hit(
            index=ANALYSIS,
            score=0.9,
            text="Client C1 — fuel section.",
            locator="analysis:fuel (C1/fuel_management_events.csv)",
            findings={"kpis": [{"name": "fuel_consumption_rate", "value": 124.006}]},
        )
        block = BedrockRAGAgent._context_block(1, hit)
        assert "Exact values" in block
        assert "fuel_consumption_rate" in block
        assert "124.006" in block

    def test_no_client_skips_analysis_index(self):
        searched: list[str] = []

        def search_impl(index, body):
            searched.append(index)
            return {"hits": {"hits": []}}

        agent, _ = _make_agent(search_impl)
        agent._retrieve("fuel efficiency", client=None)

        assert ANALYSIS not in searched  # analysis skipped when no client
        assert "csv_telemetry_vecs" not in searched  # telemetry skipped too
        assert "pdf_legal_vecs" in searched  # regulatory still searched
