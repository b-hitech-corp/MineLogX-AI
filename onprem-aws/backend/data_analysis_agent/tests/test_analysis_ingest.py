"""
test_analysis_ingest.py
=======================
Unit tests for the data-analysis vectorization ingest path (no live AWS):

  - analysis_report_serializer.render_report_chunks  (report -> parent/child docs)
  - analysis_ledger.is_client_stale                  (skip vs re-ingest decision)
  - analysis_ingestor.replace_client / _doc_source   (embed children, delete-then-bulk)

All OpenSearch/Bedrock calls are mocked.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch


# Backend root on sys.path (mirrors the other data_analysis_agent tests).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_analysis_agent.tools import analysis_ingestor
from data_analysis_agent.tools.analysis_ingestor import _doc_source, replace_client
from data_analysis_agent.tools.analysis_ledger import is_client_stale
from data_analysis_agent.tools.analysis_report_serializer import (
    AnalysisChunk,
    render_report_chunks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_report() -> dict:
    """A minimal FolderPipeline report (C1, one fuel section + overview)."""
    return {
        "folder": "C1",
        "processed_at": "2026-06-30T12:00:00Z",
        "file_count": 1,
        "overview": {
            "total_rows": 7000,
            "files": [
                {
                    "path": "C1/fuel_management_events.csv",
                    "status": "success",
                    "rows": 7000,
                    "columns": 10,
                    "errors": {},
                }
            ],
            "data_quality": [],
            "kpi_summary": {"total_computed": 1, "by_section": {"fuel": 1}},
        },
        "fuel": {
            "kpis": [
                {"name": "fuel_consumption_rate", "value": 124.006, "unit": "L/hr"},
                {
                    "name": "fuel_efficiency",
                    "status": "error",
                    "error": "Required columns missing from dataset: ['distance_km']",
                },
            ],
            "statistics": {
                "fuel_rate_lph": {"mean": 124.0, "min": 112.9, "max": 182.3, "std": 8.1}
            },
            "outliers": [
                {
                    "column": "fuel_volume_l",
                    "method": "iqr",
                    "outlier_count": 297,
                    "samples": [{"fuel_event_id": "x", "fuel_volume_l": 152.66}],
                }
            ],
            "trends": [
                {
                    "date_column": "timestamp",
                    "value_column": "fuel_rate_lph",
                    "direction": "stable",
                    "r_squared": 0.169,
                    "slope": None,
                }
            ],
            "charts": [],
        },
    }


def _render():
    return render_report_chunks(
        _fake_report(),
        content_signature="etagA",
        embed_model_id="cohere.embed-v4:0",
        pipeline_version="1",
    )


# ===========================================================================
# Serializer
# ===========================================================================


class TestSerializer:
    def test_emits_parents_and_children(self):
        chunks = _render()
        parents = [c for c in chunks if c.chunk_level == "parent"]
        children = [c for c in chunks if c.chunk_level == "child"]
        parent_ids = {c.parent_id for c in parents}

        assert "C1:fuel" in parent_ids
        assert "C1:overview" in parent_ids
        assert children, "expected per-finding child docs"
        assert all(c.client_id == "C1" for c in chunks)

    def test_fuel_parent_holds_full_section(self):
        fuel = next(
            c
            for c in _render()
            if c.parent_id == "C1:fuel" and c.chunk_level == "parent"
        )
        kf = fuel.key_findings
        assert len(kf["kpis"]) == 2
        assert len(kf["outliers"]) == 1
        assert len(kf["trends"]) == 1
        assert len(kf["headline_stats"]) == 1

    def test_errored_kpi_retained_with_reason(self):
        fuel = next(
            c
            for c in _render()
            if c.parent_id == "C1:fuel" and c.chunk_level == "parent"
        )
        errored = [k for k in fuel.key_findings["kpis"] if k.get("status") == "error"]
        assert errored and "distance_km" in errored[0]["error"]

    def test_each_finding_carries_source_files(self):
        fuel = next(
            c
            for c in _render()
            if c.parent_id == "C1:fuel" and c.chunk_level == "parent"
        )
        for group in fuel.key_findings.values():
            for finding in group:
                assert finding.get("source_files") == ["C1/fuel_management_events.csv"]

    def test_kpi_child_holds_single_finding(self):
        child = next(
            c
            for c in _render()
            if c.chunk_level == "child" and c.kpi_names == ["fuel_consumption_rate"]
        )
        assert list(child.key_findings.keys()) == ["kpis"]
        assert len(child.key_findings["kpis"]) == 1
        assert child.embedding == []  # not embedded until the ingestor runs


# ===========================================================================
# Ledger
# ===========================================================================


class TestLedger:
    RECORDS = [
        {
            "client_id": "C1",
            "source_file": "C1/f.csv",
            "content_signature": "etagA",
            "pipeline_version": "1",
            "status": "indexed",
        }
    ]

    def test_not_stale_when_all_present_and_matching(self):
        assert not is_client_stale("C1", {"C1/f.csv": "etagA"}, self.RECORDS, "1")

    def test_stale_when_file_absent(self):
        assert is_client_stale("C1", {"C1/new.csv": "etagB"}, self.RECORDS, "1")

    def test_stale_when_etag_changed(self):
        assert is_client_stale("C1", {"C1/f.csv": "etagB"}, self.RECORDS, "1")

    def test_stale_when_pipeline_version_bumped(self):
        assert is_client_stale("C1", {"C1/f.csv": "etagA"}, self.RECORDS, "2")

    def test_stale_when_last_status_not_indexed(self):
        recs = [{**self.RECORDS[0], "status": "error"}]
        assert is_client_stale("C1", {"C1/f.csv": "etagA"}, recs, "1")

    def test_no_files_is_not_stale(self):
        assert not is_client_stale("C1", {}, self.RECORDS, "1")


# ===========================================================================
# Ingestor
# ===========================================================================


class TestIngestor:
    def _chunks(self) -> list[AnalysisChunk]:
        parent = AnalysisChunk(
            chunk_level="parent",
            parent_id="C1:fuel",
            client_id="C1",
            section="fuel",
            text="fuel section",
            key_findings={"kpis": [{"name": "fuel_consumption_rate", "value": 124.0}]},
            kpi_names=["fuel_consumption_rate"],
            source_files=["C1/fuel_management_events.csv"],
            content_signature="etagA",
        )
        child = AnalysisChunk(
            chunk_level="child",
            parent_id="C1:fuel",
            client_id="C1",
            section="fuel",
            text="C1 fuel — fuel_consumption_rate = 124.0 L/hr",
            key_findings={"kpis": [{"name": "fuel_consumption_rate", "value": 124.0}]},
            kpi_names=["fuel_consumption_rate"],
            source_files=["C1/fuel_management_events.csv"],
            content_signature="etagA",
        )
        return [parent, child]

    def test_doc_source_child_has_vector_parent_does_not(self):
        parent, child = self._chunks()
        child.embedding = [0.1] * 1024
        child_doc = _doc_source(child)
        parent_doc = _doc_source(parent)
        assert child_doc["text_embedding"] == [0.1] * 1024
        assert "text_embedding" not in parent_doc
        for doc in (child_doc, parent_doc):
            assert doc["client_id"] == "C1"
            assert doc["parent_id"] == "C1:fuel"
            assert doc["chunk_level"] in ("parent", "child")
            assert doc["content_signature"] == "etagA"
            assert "key_findings" in doc

    def test_replace_client_embeds_children_and_deletes_before_bulk(self):
        chunks = self._chunks()
        mock_os = MagicMock()
        mock_os.indices.exists.return_value = True
        mock_os.search.return_value = {
            "hits": {"hits": [{"_id": f"old{i}"} for i in range(5)]}
        }

        # Record call order across search (finds docs to delete) and bulk
        # (first call deletes them by _id, second call indexes the fresh set).
        order = MagicMock()
        order.attach_mock(mock_os.search, "search")

        with (
            patch.object(
                analysis_ingestor, "_embed_texts", return_value=[[0.5] * 1024]
            ) as mock_embed,
            patch.object(
                analysis_ingestor, "os_bulk", side_effect=[(5, []), (2, [])]
            ) as mock_bulk,
        ):
            order.attach_mock(mock_bulk, "bulk")
            result = replace_client(
                "C1",
                chunks,
                client=mock_os,
                bedrock_rt=MagicMock(),
                index_name="analysis_vecs",
            )

        # Only the child was embedded (one text in, one vector out).
        assert mock_embed.call_count == 1
        assert mock_embed.call_args[0][1] == [
            "C1 fuel — fuel_consumption_rate = 124.0 L/hr"
        ]
        child = next(c for c in chunks if c.chunk_level == "child")
        parent = next(c for c in chunks if c.chunk_level == "parent")
        assert child.embedding == [0.5] * 1024
        assert parent.embedding == []

        # Prior docs found via search filtered by client_id (AOSS has no
        # _delete_by_query), then removed with a bulk delete-by-_id — before
        # the fresh bulk index.
        mock_os.search.assert_called_once()
        assert mock_os.search.call_args.kwargs["body"]["query"] == {
            "term": {"client_id": "C1"}
        }
        delete_actions = mock_bulk.call_args_list[0][0][1]
        assert delete_actions == [
            {"_op_type": "delete", "_index": "analysis_vecs", "_id": f"old{i}"}
            for i in range(5)
        ]
        call_names = [name for name, _, _ in order.mock_calls]
        assert call_names.index("search") < call_names.index("bulk")

        assert result.documents_indexed == 2
        assert result.documents_deleted == 5
