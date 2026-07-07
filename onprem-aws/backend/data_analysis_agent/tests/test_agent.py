"""
Tests for FleetAgent orchestrator.
Run with: pytest tests/test_agent.py -v

All tests are fully mocked — no EC2 endpoint or CSV file required.
For live tests against the real model, see test_agent_integration.py.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch, MagicMock

import data_analysis_agent.agent.orchestrator as orch
from data_analysis_agent.agent.orchestrator import FleetAgent, AgentResult
from data_analysis_agent.config.settings import settings

FILE = "fleet_may_2024.csv"


# ---------------------------------------------------------------------------
# FleetAgent initialisation
# ---------------------------------------------------------------------------


class TestFleetAgentInit:
    def test_ollama_model_receives_configured_endpoint(self):
        with patch("data_analysis_agent.agent.orchestrator.OllamaModel") as MockModel:
            FleetAgent()
            call_kwargs = MockModel.call_args.kwargs
            assert "host" in call_kwargs
            assert call_kwargs["host"].startswith("http://")

    def test_ollama_model_receives_configured_model(self):
        with patch("data_analysis_agent.agent.orchestrator.OllamaModel") as MockModel:
            FleetAgent()
            call_kwargs = MockModel.call_args.kwargs
            assert "model_id" in call_kwargs
            assert call_kwargs["model_id"] == settings.ollama.model

    def test_max_turns_from_settings(self):
        with patch("data_analysis_agent.agent.orchestrator.OllamaModel"):
            agent = FleetAgent()
            assert agent.max_turns > 0


# ---------------------------------------------------------------------------
# FleetAgent.run()
# ---------------------------------------------------------------------------


class TestFleetAgentRun:
    @pytest.fixture
    def fleet_agent(self):
        with patch("data_analysis_agent.agent.orchestrator.OllamaModel"):
            return FleetAgent()

    def _mock_agent(self, MockAgent, response_text: str):
        instance = MagicMock()
        instance.return_value = response_text
        MockAgent.return_value = instance
        return instance

    def test_returns_agent_result_instance(self, fleet_agent):
        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "Fleet analysis complete.")
            result = fleet_agent.run("Summarise fleet performance")
            assert isinstance(result, AgentResult)

    def test_summary_is_model_response(self, fleet_agent):
        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "Top vehicle is V003 with 8.1 km/L.")
            result = fleet_agent.run("Who is the most fuel-efficient vehicle?")
            assert result.summary == "Top vehicle is V003 with 8.1 km/L."

    def test_charts_empty_when_no_chart_tools_called(self, fleet_agent):
        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "There are 12 vehicles.")
            result = fleet_agent.run("How many vehicles are in the fleet?")
            assert result.charts == []

    def test_run_resets_charts_from_previous_run(self, fleet_agent):
        orch._run_charts = [{"chart_type": "LineChart", "title": "Stale chart"}]
        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "Done.")
            result = fleet_agent.run("Any question")
            assert result.charts == []

    def test_charts_collected_from_run_charts(self, fleet_agent):
        fake_spec = {"chart_type": "BarChart", "title": "Fuel by vehicle"}

        def side_effect(prompt):
            orch._run_charts.append(fake_spec)
            return "Here is the bar chart."

        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            instance = MagicMock()
            instance.side_effect = side_effect
            MockAgent.return_value = instance

            result = fleet_agent.run("Show fuel consumption per vehicle")

        assert len(result.charts) == 1
        assert result.charts[0]["chart_type"] == "BarChart"

    def test_multiple_charts_all_returned(self, fleet_agent):
        specs = [
            {"chart_type": "LineChart", "title": "Trend"},
            {"chart_type": "BarChart", "title": "Ranking"},
        ]

        def side_effect(prompt):
            orch._run_charts.extend(specs)
            return "Two charts built."

        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            instance = MagicMock()
            instance.side_effect = side_effect
            MockAgent.return_value = instance

            result = fleet_agent.run("Show trend and ranking")

        assert len(result.charts) == 2

    def test_agent_receives_system_prompt(self, fleet_agent):
        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "Done.")
            fleet_agent.run("Any question")
            _, kwargs = MockAgent.call_args
            assert len(kwargs.get("system_prompt", "")) > 0

    def test_agent_receives_full_tool_list(self, fleet_agent):
        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "Done.")
            fleet_agent.run("Any question")
            _, kwargs = MockAgent.call_args
            tools = kwargs.get("tools", [])
            tool_names = [t.__name__ for t in tools]
            assert "csv_loader__load_csv" in tool_names
            assert "kpi_engine__calculate_kpi" in tool_names
            assert "chart_spec_builder__build_line_chart" in tool_names
            assert len(tools) == len(orch._TOOLS)

    def test_successive_runs_do_not_share_charts(self, fleet_agent):
        def side_effect_first(prompt):
            orch._run_charts.append({"chart_type": "PieChart", "title": "First run"})
            return "First."

        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            instance = MagicMock()
            instance.side_effect = side_effect_first
            MockAgent.return_value = instance
            fleet_agent.run("First question")

        with patch("data_analysis_agent.agent.orchestrator.Agent") as MockAgent:
            self._mock_agent(MockAgent, "Second.")
            result = fleet_agent.run("Second question")

        assert result.charts == []


# ---------------------------------------------------------------------------
# Chart tool capture
# ---------------------------------------------------------------------------


class TestChartToolCapture:
    """Chart @tool wrappers must append specs to _run_charts."""

    def setup_method(self):
        orch._run_charts = []

    def test_line_chart_appended(self):
        orch.chart_spec_builder__build_line_chart(
            title="Distance over time",
            data=[{"date": "2024-05-01", "distance_km": 210}],
            x_key="date",
            y_keys=["distance_km"],
        )
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "LineChart"
        assert orch._run_charts[0]["title"] == "Distance over time"

    def test_bar_chart_appended(self):
        orch.chart_spec_builder__build_bar_chart(
            title="Fuel by vehicle",
            data=[{"vehicle_id": "V001", "fuel_litres": 28.5}],
            x_key="vehicle_id",
            y_keys=["fuel_litres"],
        )
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "BarChart"

    def test_kpi_cards_appended(self):
        orch.chart_spec_builder__build_kpi_cards(
            title="Fleet KPIs",
            kpis=[
                {
                    "label": "Fuel Efficiency",
                    "value": 7.8,
                    "unit": "km/L",
                    "trend": "up",
                }
            ],
        )
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "KPICards"

    def test_pie_chart_appended(self):
        orch.chart_spec_builder__build_pie_chart(
            title="Region split",
            data=[{"name": "North", "value": 5}, {"name": "South", "value": 6}],
        )
        assert len(orch._run_charts) == 1
        assert orch._run_charts[0]["chart_type"] == "PieChart"

    def test_multiple_charts_accumulated_in_order(self):
        orch.chart_spec_builder__build_line_chart(
            title="Line",
            data=[{"date": "2024-05-01", "v": 1}],
            x_key="date",
            y_keys=["v"],
        )
        orch.chart_spec_builder__build_bar_chart(
            title="Bar", data=[{"id": "A", "v": 2}], x_key="id", y_keys=["v"]
        )
        orch.chart_spec_builder__build_kpi_cards(
            title="KPIs", kpis=[{"label": "Idle rate", "value": 12.5, "unit": "%"}]
        )
        assert len(orch._run_charts) == 3
        assert orch._run_charts[0]["chart_type"] == "LineChart"
        assert orch._run_charts[1]["chart_type"] == "BarChart"
        assert orch._run_charts[2]["chart_type"] == "KPICards"

    def test_chart_spec_contains_data(self):
        data = [{"date": "2024-05-01", "fuel_litres": 120.5}]
        orch.chart_spec_builder__build_line_chart(
            title="Fuel trend", data=data, x_key="date", y_keys=["fuel_litres"]
        )
        spec = orch._run_charts[0]
        assert spec["data"] == data
        assert spec["x_axis"]["key"] == "date"


# ---------------------------------------------------------------------------
# Tool wrapper delegation
# ---------------------------------------------------------------------------


class TestToolWrappers:
    """Each @tool wrapper must delegate to its underlying tool module function."""

    def test_csv_loader_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.csv_loader.load_csv",
            return_value={"row_count": 15},
        ) as mock_fn:
            result = orch.csv_loader__load_csv(file_path=FILE, use_local_fallback=True)
            mock_fn.assert_called_once_with(
                file_path=FILE,
                date_columns=None,
                use_local_fallback=True,
            )
            assert result["row_count"] == 15

    def test_kpi_calculate_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.kpi_engine.calculate_kpi",
            return_value={"kpis": {}},
        ) as mock_fn:
            orch.kpi_engine__calculate_kpi(
                file_path=FILE, kpi_names=["fuel_efficiency"]
            )
            mock_fn.assert_called_once_with(
                file_path=FILE,
                kpi_names=["fuel_efficiency"],
                group_by=None,
                filter_expr=None,
            )

    def test_kpi_available_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.kpi_engine.available_kpis",
            return_value={"available_kpis": []},
        ) as mock_fn:
            orch.kpi_engine__available_kpis()
            mock_fn.assert_called_once()

    def test_describe_columns_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.stats_analyzer.describe_columns",
            return_value={},
        ) as mock_fn:
            orch.stats_analyzer__describe_columns(file_path=FILE)
            mock_fn.assert_called_once_with(file_path=FILE, columns=None)

    def test_rank_entities_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.stats_analyzer.rank_entities",
            return_value={},
        ) as mock_fn:
            orch.stats_analyzer__rank_entities(
                file_path=FILE, metric_column="fuel_litres", entity_column="vehicle_id"
            )
            mock_fn.assert_called_once_with(
                file_path=FILE,
                metric_column="fuel_litres",
                entity_column="vehicle_id",
                top_n=10,
                ascending=False,
                agg_func="mean",
            )

    def test_detect_outliers_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.insight_extractor.detect_outliers",
            return_value={},
        ) as mock_fn:
            orch.insight_extractor__detect_outliers(file_path=FILE, column="idle_hours")
            mock_fn.assert_called_once_with(
                file_path=FILE,
                column="idle_hours",
                method="iqr",
                threshold=1.5,
                entity_column=None,
            )

    def test_detect_trend_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.insight_extractor.detect_trend",
            return_value={},
        ) as mock_fn:
            orch.insight_extractor__detect_trend(
                file_path=FILE, date_column="date", value_column="distance_km"
            )
            mock_fn.assert_called_once_with(
                file_path=FILE,
                date_column="date",
                value_column="distance_km",
                freq="W",
            )

    def test_check_thresholds_delegates(self):
        rules = [{"column": "idle_hours", "operator": ">", "value": 2.0}]
        with patch(
            "data_analysis_agent.agent.orchestrator.insight_extractor.check_thresholds",
            return_value={},
        ) as mock_fn:
            orch.insight_extractor__check_thresholds(file_path=FILE, rules=rules)
            mock_fn.assert_called_once_with(file_path=FILE, rules=rules)

    def test_fleet_performance_summary_delegates(self):
        with patch(
            "data_analysis_agent.agent.orchestrator.insight_extractor.fleet_performance_summary",
            return_value={},
        ) as mock_fn:
            orch.insight_extractor__fleet_performance_summary(
                file_path=FILE, metric_column="fuel_litres", entity_column="vehicle_id"
            )
            mock_fn.assert_called_once_with(
                file_path=FILE,
                metric_column="fuel_litres",
                entity_column="vehicle_id",
                top_n=5,
            )
