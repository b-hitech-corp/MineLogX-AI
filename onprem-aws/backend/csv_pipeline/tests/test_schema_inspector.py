"""
Unit tests for schema_inspector.py's Bedrock LLM call (Stage 1 of the CSV
Vectorization Pipeline). All tests are fully mocked — no AWS credentials or
API calls required.

Run with: pytest csv_pipeline/tests/test_schema_inspector.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch

import pytest

from csv_pipeline.config.settings import settings
from csv_pipeline.tools.schema_inspector import (
    _empty_inspect_result,
    _extract_json,
    _llm_complete,
    inspect_schema_with_tool_use,
)


# ---------------------------------------------------------------------------
# Response builders — plain dicts matching invoke_claude's return shape
# (the parsed Bedrock invoke_model body: {"stop_reason": ..., "content": [...]})
# ---------------------------------------------------------------------------


def _text_response(text: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def _tool_use_response(name: str, tool_id: str, inputs: dict) -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "name": name, "id": tool_id, "input": inputs}],
    }


# ---------------------------------------------------------------------------
# inspect_schema_with_tool_use — the LIVE call path (forced tool_choice)
# ---------------------------------------------------------------------------


class TestInspectSchemaWithToolUse:
    def test_happy_path_returns_tool_input(self):
        expected = {
            "column_classifications": [
                {
                    "name": "truck_id",
                    "role": "entity",
                    "kpi_variable": None,
                    "confidence": "high",
                }
            ],
            "transformation_steps": [],
            "has_structural_anomalies": False,
            "anomaly_description": None,
            "reasoning": "Clean flat table.",
        }
        with patch("csv_pipeline.tools.schema_inspector.invoke_claude") as mock_invoke:
            mock_invoke.return_value = _tool_use_response(
                "describe_csv_structure", "tool_1", expected
            )
            result = inspect_schema_with_tool_use(
                "compact profile text", backend="bedrock"
            )

        assert result == expected

    def test_passes_model_id_system_tools_and_forced_tool_choice(self):
        with patch("csv_pipeline.tools.schema_inspector.invoke_claude") as mock_invoke:
            mock_invoke.return_value = _tool_use_response(
                "describe_csv_structure",
                "tool_1",
                {
                    "column_classifications": [],
                    "transformation_steps": [],
                    "has_structural_anomalies": False,
                    "anomaly_description": None,
                    "reasoning": "ok",
                },
            )
            inspect_schema_with_tool_use("compact profile text", backend="bedrock")

        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["model_id"] == settings.bedrock.model_id
        assert isinstance(kwargs["system"], str) and len(kwargs["system"]) > 0
        assert kwargs["tools"][0]["name"] == "describe_csv_structure"
        assert kwargs["tool_choice"] == {
            "type": "tool",
            "name": "describe_csv_structure",
        }

    def test_missing_tool_use_block_falls_back_to_empty_result(self):
        """tool_choice should guarantee a tool_use block; guard defensively anyway."""
        with patch("csv_pipeline.tools.schema_inspector.invoke_claude") as mock_invoke:
            mock_invoke.return_value = _text_response("I decided not to call a tool.")
            result = inspect_schema_with_tool_use(
                "compact profile text", backend="bedrock"
            )

        assert result["column_classifications"] == []
        assert result["transformation_steps"] == []
        assert "LLM call failed" in result["reasoning"]

    def test_invoke_claude_exception_falls_back_to_empty_result(self):
        with patch(
            "csv_pipeline.tools.schema_inspector.invoke_claude",
            side_effect=RuntimeError("ThrottlingException"),
        ):
            result = inspect_schema_with_tool_use(
                "compact profile text", backend="bedrock"
            )

        assert result == _empty_inspect_result("ThrottlingException")

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            inspect_schema_with_tool_use(
                "compact profile text", backend="not-a-backend"
            )


# ---------------------------------------------------------------------------
# _llm_complete — bedrock branch is unreachable in production (only ever
# called with backend="ollama" today) but is migrated for behavior parity.
# ---------------------------------------------------------------------------


class TestLlmCompleteBedrockBranch:
    def test_returns_stripped_text_on_success(self):
        with patch("csv_pipeline.tools.schema_inspector.invoke_claude") as mock_invoke:
            mock_invoke.return_value = _text_response("  hello world  \n")
            result = _llm_complete("a prompt", backend="bedrock")

        assert result == "hello world"

    def test_passes_model_id(self):
        with patch("csv_pipeline.tools.schema_inspector.invoke_claude") as mock_invoke:
            mock_invoke.return_value = _text_response("ok")
            _llm_complete("a prompt", backend="bedrock", max_tokens=256)

        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["model_id"] == settings.bedrock.model_id
        assert kwargs["max_tokens"] == 256

    def test_returns_none_on_exception(self):
        with patch(
            "csv_pipeline.tools.schema_inspector.invoke_claude",
            side_effect=RuntimeError("boom"),
        ):
            result = _llm_complete("a prompt", backend="bedrock")

        assert result is None


# ---------------------------------------------------------------------------
# _extract_json — pure/deterministic, previously had zero coverage
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_fenced_json_code_block(self):
        text = 'Here is the result:\n```json\n{"a": 1, "b": 2}\n```\nDone.'
        assert _extract_json(text) == {"a": 1, "b": 2}

    def test_bare_json_object(self):
        text = '{"a": 1, "b": 2}'
        assert _extract_json(text) == {"a": 1, "b": 2}

    def test_json_object_with_surrounding_prose(self):
        text = 'Sure, here you go: {"a": 1, "nested": {"b": 2}} — hope that helps!'
        assert _extract_json(text) == {"a": 1, "nested": {"b": 2}}

    def test_empty_or_none_text_returns_none(self):
        assert _extract_json("") is None
        assert _extract_json(None) is None

    def test_unparseable_text_returns_none(self):
        assert _extract_json("no json here at all") is None
