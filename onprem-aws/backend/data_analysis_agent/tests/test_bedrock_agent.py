"""
Unit tests for the Bedrock FleetAgent orchestrator.
All tests are fully mocked — no AWS credentials or API calls required.

Run with: pytest tests/test_bedrock_agent.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import patch, MagicMock

import data_analysis_agent.agent.bedrock_orchestrator as orch
from data_analysis_agent.agent.bedrock_orchestrator import (
    FleetAgent,
    AgentResult,
    _dispatch,
)
from data_analysis_agent.config.settings import settings


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _end_turn(text: str) -> MagicMock:
    """Mock Anthropic response with stop_reason='end_turn'."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _tool_use(name: str, tool_id: str, inputs: dict) -> MagicMock:
    """Mock Anthropic response with stop_reason='tool_use'."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = inputs
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# FleetAgent.__init__
# ---------------------------------------------------------------------------


class TestFleetAgentInit:
    def test_bedrock_client_created_with_configured_region(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.anthropic.AnthropicBedrock"
        ) as MockClient:
            FleetAgent()
            MockClient.assert_called_once_with(aws_region=settings.bedrock.region)

    def test_max_turns_from_settings(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.anthropic.AnthropicBedrock"
        ):
            agent = FleetAgent()
            assert agent.max_turns == settings.bedrock.max_agent_turns
            assert agent.max_turns > 0


# ---------------------------------------------------------------------------
# FleetAgent.run()
# ---------------------------------------------------------------------------


class TestFleetAgentRun:
    @pytest.fixture
    def agent(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.anthropic.AnthropicBedrock"
        ):
            return FleetAgent()

    def test_returns_agent_result_instance(self, agent):
        agent.client.messages.create.return_value = _end_turn("Done.")
        result = agent.run("Summarise fleet performance")
        assert isinstance(result, AgentResult)

    def test_summary_is_text_from_end_turn(self, agent):
        agent.client.messages.create.return_value = _end_turn("Top vehicle is V003.")
        result = agent.run("Who is the most fuel-efficient vehicle?")
        assert result.summary == "Top vehicle is V003."

    def test_charts_empty_when_no_chart_tools_called(self, agent):
        agent.client.messages.create.return_value = _end_turn("12 vehicles.")
        result = agent.run("How many vehicles?")
        assert result.charts == []

    def test_run_resets_charts_from_previous_run(self, agent):
        orch._run_charts = [{"chart_type": "LineChart", "title": "Stale"}]
        agent.client.messages.create.return_value = _end_turn("Done.")
        result = agent.run("Any question")
        assert result.charts == []

    def test_successive_runs_do_not_share_charts(self, agent):
        def first_run(*args, **kwargs):
            orch._run_charts.append({"chart_type": "BarChart", "title": "Run 1"})
            return _end_turn("First.")

        agent.client.messages.create.side_effect = first_run
        agent.run("First question")

        agent.client.messages.create.side_effect = None
        agent.client.messages.create.return_value = _end_turn("Second.")
        result = agent.run("Second question")
        assert result.charts == []

    def test_tool_call_logged(self, agent):
        agent.client.messages.create.side_effect = [
            _tool_use("kpi_engine__available_kpis", "t1", {}),
            _end_turn("KPIs listed."),
        ]
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator._dispatch",
            return_value={"available_kpis": []},
        ):
            result = agent.run("What KPIs are available?")

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["tool"] == "kpi_engine__available_kpis"

    def test_turns_incremented_per_api_call(self, agent):
        agent.client.messages.create.side_effect = [
            _tool_use("kpi_engine__available_kpis", "t1", {}),
            _end_turn("Done."),
        ]
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator._dispatch", return_value={}
        ):
            result = agent.run("Any question")
        assert result.turns == 2

    def test_model_id_passed_to_api(self, agent):
        agent.client.messages.create.return_value = _end_turn("Done.")
        agent.run("Any question")
        kwargs = agent.client.messages.create.call_args.kwargs
        assert kwargs["model"] == settings.bedrock.model_id

    def test_system_prompt_passed_to_api(self, agent):
        agent.client.messages.create.return_value = _end_turn("Done.")
        agent.run("Any question")
        kwargs = agent.client.messages.create.call_args.kwargs
        assert isinstance(kwargs["system"], str)
        assert len(kwargs["system"]) > 0

    def test_first_message_is_user_role(self, agent):
        agent.client.messages.create.return_value = _end_turn("Done.")
        agent.run("How many vehicles?")
        messages = agent.client.messages.create.call_args.kwargs["messages"]
        assert messages[0]["role"] == "user"

    def test_tool_result_sent_as_user_message(self, agent):
        agent.client.messages.create.side_effect = [
            _tool_use("kpi_engine__available_kpis", "tool_abc", {}),
            _end_turn("Done."),
        ]
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator._dispatch",
            return_value={"available_kpis": []},
        ):
            agent.run("List KPIs")

        second_call_messages = agent.client.messages.create.call_args_list[1].kwargs[
            "messages"
        ]
        last_msg = second_call_messages[-1]
        assert last_msg["role"] == "user"
        assert last_msg["content"][0]["type"] == "tool_result"
        assert last_msg["content"][0]["tool_use_id"] == "tool_abc"

    def test_dispatch_exception_does_not_raise(self, agent):
        agent.client.messages.create.side_effect = [
            _tool_use("kpi_engine__available_kpis", "t1", {}),
            _end_turn("Done anyway."),
        ]
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator._dispatch",
            side_effect=ValueError("fail"),
        ):
            result = agent.run("List KPIs")
        assert isinstance(result, AgentResult)

    def test_charts_collected_from_chart_tool(self, agent):
        fake_spec = {"chart_type": "BarChart", "title": "Fuel"}

        def fake_dispatch(name, inputs):
            if name == "chart_spec_builder__build_bar_chart":
                orch._run_charts.append(fake_spec)
                return fake_spec
            return {}

        agent.client.messages.create.side_effect = [
            _tool_use(
                "chart_spec_builder__build_bar_chart",
                "t1",
                {"title": "Fuel", "data": [], "x_key": "id", "y_keys": ["v"]},
            ),
            _end_turn("Chart built."),
        ]
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator._dispatch",
            side_effect=fake_dispatch,
        ):
            result = agent.run("Show fuel by vehicle")

        assert len(result.charts) == 1
        assert result.charts[0]["chart_type"] == "BarChart"


# ---------------------------------------------------------------------------
# Chart capture via _dispatch
# ---------------------------------------------------------------------------


class TestChartCapture:
    """Chart builder calls inside _dispatch must append specs to _run_charts."""

    def setup_method(self):
        orch._run_charts = []

    def test_line_chart_captured(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.chart_spec_builder.build_line_chart",
            return_value={"chart_type": "LineChart", "title": "T", "data": []},
        ):
            _dispatch(
                "chart_spec_builder__build_line_chart",
                {"title": "T", "data": [], "x_key": "date", "y_keys": ["v"]},
            )
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "LineChart"

    def test_bar_chart_captured(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.chart_spec_builder.build_bar_chart",
            return_value={"chart_type": "BarChart", "title": "T", "data": []},
        ):
            _dispatch(
                "chart_spec_builder__build_bar_chart",
                {"title": "T", "data": [], "x_key": "id", "y_keys": ["v"]},
            )
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "BarChart"

    def test_kpi_cards_captured(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.chart_spec_builder.build_kpi_cards",
            return_value={"chart_type": "KPICards", "title": "T", "cards": []},
        ):
            _dispatch("chart_spec_builder__build_kpi_cards", {"title": "T", "kpis": []})
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "KPICards"

    def test_pie_chart_captured(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.chart_spec_builder.build_pie_chart",
            return_value={"chart_type": "PieChart", "title": "T", "data": []},
        ):
            _dispatch("chart_spec_builder__build_pie_chart", {"title": "T", "data": []})
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "PieChart"

    def test_multiple_charts_accumulated_in_order(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.chart_spec_builder.build_line_chart",
            return_value={"chart_type": "LineChart", "title": "L", "data": []},
        ):
            _dispatch(
                "chart_spec_builder__build_line_chart",
                {"title": "L", "data": [], "x_key": "d", "y_keys": ["v"]},
            )
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.chart_spec_builder.build_bar_chart",
            return_value={"chart_type": "BarChart", "title": "B", "data": []},
        ):
            _dispatch(
                "chart_spec_builder__build_bar_chart",
                {"title": "B", "data": [], "x_key": "id", "y_keys": ["v"]},
            )
        assert len(orch._run_charts) == 2
        assert orch._run_charts[0]["chart_type"] == "LineChart"
        assert orch._run_charts[1]["chart_type"] == "BarChart"


# ---------------------------------------------------------------------------
# _dispatch routing
# ---------------------------------------------------------------------------


class TestDispatch:
    """_dispatch must route each tool name to the correct module function."""

    def test_csv_loader_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.csv_loader.load_csv",
            return_value={"row_count": 10},
        ) as m:
            result = _dispatch("csv_loader__load_csv", {"file_path": "f.csv"})
            m.assert_called_once_with(file_path="f.csv")
            assert result["row_count"] == 10

    def test_schema_advisor_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.schema_advisor.discover_schema",
            return_value={"feasible_kpis": []},
        ) as m:
            _dispatch("schema_advisor__discover_schema", {"file_path": "f.csv"})
            m.assert_called_once_with(file_path="f.csv")

    def test_kpi_available_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.kpi_engine.available_kpis",
            return_value={"available_kpis": []},
        ) as m:
            _dispatch("kpi_engine__available_kpis", {})
            m.assert_called_once()

    def test_kpi_calculate_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.kpi_engine.calculate_kpi",
            return_value={"kpis": {}},
        ) as m:
            _dispatch(
                "kpi_engine__calculate_kpi",
                {"file_path": "f.csv", "kpi_names": ["fuel_efficiency"]},
            )
            m.assert_called_once_with(file_path="f.csv", kpi_names=["fuel_efficiency"])

    def test_describe_columns_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.stats_analyzer.describe_columns",
            return_value={},
        ) as m:
            _dispatch("stats_analyzer__describe_columns", {"file_path": "f.csv"})
            m.assert_called_once_with(file_path="f.csv")

    def test_rank_entities_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.stats_analyzer.rank_entities",
            return_value={},
        ) as m:
            _dispatch(
                "stats_analyzer__rank_entities",
                {"file_path": "f.csv", "metric_column": "fuel", "entity_column": "id"},
            )
            m.assert_called_once()

    def test_detect_outliers_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.insight_extractor.detect_outliers",
            return_value={},
        ) as m:
            _dispatch(
                "insight_extractor__detect_outliers",
                {"file_path": "f.csv", "column": "idle_hours"},
            )
            m.assert_called_once_with(file_path="f.csv", column="idle_hours")

    def test_detect_trend_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.insight_extractor.detect_trend",
            return_value={},
        ) as m:
            _dispatch(
                "insight_extractor__detect_trend",
                {"file_path": "f.csv", "date_column": "date", "value_column": "v"},
            )
            m.assert_called_once()

    def test_check_thresholds_delegates(self):
        rules = [{"column": "idle_hours", "operator": ">", "value": 2.0}]
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.insight_extractor.check_thresholds",
            return_value={},
        ) as m:
            _dispatch(
                "insight_extractor__check_thresholds",
                {"file_path": "f.csv", "rules": rules},
            )
            m.assert_called_once_with(file_path="f.csv", rules=rules)

    def test_fleet_performance_summary_delegates(self):
        with patch(
            "data_analysis_agent.agent.bedrock_orchestrator.insight_extractor.fleet_performance_summary",
            return_value={},
        ) as m:
            _dispatch(
                "insight_extractor__fleet_performance_summary",
                {"file_path": "f.csv", "metric_column": "fuel", "entity_column": "id"},
            )
            m.assert_called_once()

    def test_unknown_tool_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            _dispatch("nonexistent__tool", {})
