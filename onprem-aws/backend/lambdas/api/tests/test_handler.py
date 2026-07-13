"""
Unit tests for the /analyze route in lambdas/api/handler.py.

_handle_analyze accepts a client/company id (not a question) and returns the
FolderPipeline report directly, matching what shared/frontend/src/services/
company.ts expects. _get_folder_pipeline is monkeypatched so these tests never
construct a real FolderPipeline/Strands agent or touch Bedrock.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# handler.py has no package __init__.py — bootstrap both the api/ dir (for a
# bare `import handler`) and backend/ (so handler's lazy imports, e.g.
# data_analysis_agent.agent.pipeline, would resolve if not monkeypatched).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import handler  # noqa: E402


def _event(body: dict) -> dict:
    return {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/analyze",
        "body": json.dumps(body),
    }


@pytest.fixture(autouse=True)
def _reset_pipeline_singleton():
    handler._folder_pipeline = None
    yield
    handler._folder_pipeline = None


def test_analyze_accepts_company_and_returns_report_directly(monkeypatch):
    fake_report = {"folder": "C1", "file_count": 2, "overview": {"total_rows": 10}}
    fake_pipeline = MagicMock()
    fake_pipeline.run.return_value = fake_report
    monkeypatch.setattr(handler, "_get_folder_pipeline", lambda: fake_pipeline)

    response = handler._handle_analyze(_event({"company": "c1"}))

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body == fake_report
    assert "success" not in body  # not the old FleetAgent wrapper shape
    fake_pipeline.run.assert_called_once_with("C1")  # normalized to upper-case


def test_analyze_rejects_missing_company():
    response = handler._handle_analyze(_event({}))
    assert response["statusCode"] == 400


@pytest.mark.parametrize(
    "bad_company", ["../etc/passwd", "C1/../C2", "", "   ", "a" * 65]
)
def test_analyze_rejects_invalid_company(bad_company):
    response = handler._handle_analyze(_event({"company": bad_company}))
    assert response["statusCode"] == 400


def test_analyze_returns_502_on_pipeline_error(monkeypatch):
    fake_pipeline = MagicMock()
    fake_pipeline.run.side_effect = RuntimeError("boom")
    monkeypatch.setattr(handler, "_get_folder_pipeline", lambda: fake_pipeline)

    response = handler._handle_analyze(_event({"company": "C1"}))

    assert response["statusCode"] == 502
