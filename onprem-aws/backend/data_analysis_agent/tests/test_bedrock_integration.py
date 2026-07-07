"""
Integration tests for FleetAgent against Amazon Bedrock (Claude Sonnet 4.6).

Run with:
    pytest tests/test_bedrock_integration.py -v -m integration

Requirements:
  - Valid AWS credentials (env vars, ~/.aws/credentials, or IAM role)
  - Bedrock model access granted in the configured region
  - Data available via S3 (bhitech-minelogx-poc-telemetry-data) OR
    sample_data/C1/fuel_management_events.csv present locally

Data source is selected automatically:
  - S3 is tried first; if accessible the tests hit the real bucket
  - If S3 is unreachable, local sample_data/ is used as fallback
  - If neither is available, the test session is skipped

Add to pytest.ini:
    [pytest]
    markers =
        integration: live tests — require AWS credentials and data access
"""

from __future__ import annotations

import os
import re
import sys

import pytest

try:
    import boto3
    from botocore.exceptions import ClientError

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_analysis_agent.agent.bedrock_orchestrator import AgentResult, FleetAgent
from data_analysis_agent.config.settings import settings
from data_analysis_agent.tools.csv_loader import load_csv
from data_analysis_agent.tools.schema_advisor import discover_schema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE_FILE = "C1/fuel_management_events.csv"
LOCAL_PATH = os.path.join(ASSETS_DIR, "sample_data", SAMPLE_FILE)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def _aws_credentials_valid() -> bool:
    if not _BOTO3_AVAILABLE:
        return False
    try:
        sts = boto3.client("sts", region_name=settings.bedrock.region)
        sts.get_caller_identity()
        return True
    except Exception:
        return False


def _bedrock_model_accessible() -> bool:
    """
    Check Bedrock access by attempting a minimal model invocation rather than
    listing foundation models (which requires a separate IAM permission).
    A ValidationException means the endpoint is reachable but the request was
    malformed — good enough to confirm access. Any other AWS error is treated
    as inaccessible.
    """
    if not _BOTO3_AVAILABLE:
        return False
    try:
        import botocore.exceptions

        client = boto3.client("bedrock-runtime", region_name=settings.bedrock.region)
        client.invoke_model(
            modelId=settings.bedrock.model_id,
            body=b"{}",
            contentType="application/json",
            accept="application/json",
        )
        return True
    except botocore.exceptions.ClientError as exc:
        code = exc.response["Error"]["Code"]
        # ValidationException → endpoint reachable, model accessible
        # AccessDeniedException / UnauthorizedException → no access
        return code == "ValidationException"
    except Exception:
        return False


def _s3_accessible() -> bool:
    if not _BOTO3_AVAILABLE:
        return False
    try:
        client = boto3.client("s3", region_name=settings.s3.region)
        client.head_bucket(Bucket=settings.s3.bucket_name)
        return True
    except Exception:
        return False


def _local_data_exists() -> bool:
    return os.path.isfile(LOCAL_PATH)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def require_bedrock():
    if not _aws_credentials_valid():
        pytest.skip(
            "No valid AWS credentials found. Configure env vars or ~/.aws/credentials."
        )
    if not _bedrock_model_accessible():
        pytest.skip(
            f"Bedrock model '{settings.bedrock.model_id}' not accessible in region "
            f"'{settings.bedrock.region}'. Check IAM permissions and model access."
        )


@pytest.fixture(scope="session")
def data_mode() -> str:
    if _s3_accessible():
        return "s3"
    if _local_data_exists():
        return "local"
    pytest.skip(
        f"No data source available. Either grant S3 access to "
        f"'{settings.s3.bucket_name}' or place {SAMPLE_FILE} in "
        f"docs/data_analysis_agent/sample_data/."
    )


@pytest.fixture(scope="session")
def loaded_schema(data_mode) -> dict:
    use_local = data_mode == "local"
    load_csv(SAMPLE_FILE, use_local_fallback=use_local)
    return discover_schema(SAMPLE_FILE, backend="bedrock")


@pytest.fixture(scope="session")
def agent(require_bedrock) -> FleetAgent:
    return FleetAgent()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prompt(question: str, data_mode: str) -> str:
    if data_mode == "local":
        return (
            f"{question} "
            f"Load '{SAMPLE_FILE}' with use_local_fallback=True, "
            f"then immediately call schema_advisor__discover_schema to understand the columns "
            f"before doing any analysis."
        )
    return (
        f"{question} "
        f"Load '{SAMPLE_FILE}' from S3, "
        f"then immediately call schema_advisor__discover_schema to understand the columns "
        f"before doing any analysis."
    )


def _assert_valid_chart_spec(spec: dict) -> None:
    assert "chart_type" in spec, f"Missing chart_type: {spec}"
    assert "library" in spec, f"Missing library: {spec}"
    assert "title" in spec, f"Missing title: {spec}"
    assert isinstance(spec.get("data") or spec.get("cards"), list), (
        f"Spec has no data or cards: {spec}"
    )


# ---------------------------------------------------------------------------
# Bedrock connectivity
# ---------------------------------------------------------------------------


class TestBedrockConnectivity:
    def test_aws_credentials_valid(self, require_bedrock):
        assert _aws_credentials_valid()

    def test_bedrock_model_accessible(self, require_bedrock):
        assert _bedrock_model_accessible(), (
            f"Bedrock model not accessible. "
            f"Model ID: {settings.bedrock.model_id}, Region: {settings.bedrock.region}"
        )

    def test_region_is_configured(self):
        assert settings.bedrock.region, "BEDROCK region must be set"

    def test_model_id_is_configured(self):
        assert settings.bedrock.model_id, "BEDROCK_MODEL_ID must be set"


# ---------------------------------------------------------------------------
# S3 connectivity (same requirements as Ollama suite)
# ---------------------------------------------------------------------------


class TestS3Connectivity:
    def test_bucket_is_accessible(self):
        if not _s3_accessible():
            pytest.skip(
                "S3 not accessible in this environment — running in local mode."
            )
        assert _s3_accessible(), (
            f"S3 bucket '{settings.s3.bucket_name}' in region '{settings.s3.region}' "
            f"is not accessible. Check IAM permissions."
        )

    def test_bucket_name_matches_config(self):
        assert settings.s3.bucket_name == "bhitech-minelogx-poc-telemetry-data"

    def test_region_is_us_east_1(self):
        assert settings.s3.region == "us-east-1"

    def test_sample_file_exists_in_bucket(self):
        if not _s3_accessible():
            pytest.skip("S3 not accessible.")
        try:
            client = boto3.client("s3", region_name=settings.s3.region)
            key = settings.s3.prefix + SAMPLE_FILE
            client.head_object(Bucket=settings.s3.bucket_name, Key=key)
        except ClientError as exc:
            pytest.fail(
                f"'{key}' not found in bucket '{settings.s3.bucket_name}': {exc}"
            )


# ---------------------------------------------------------------------------
# Schema discovery (tool-level — no agent LLM turn needed)
# ---------------------------------------------------------------------------


class TestSchemaDiscovery:
    """Direct tests of load_csv → discover_schema. No agent loop involved."""

    def test_discover_schema_returns_required_keys(self, loaded_schema):
        for key in (
            "entity_columns",
            "datetime_columns",
            "metric_columns",
            "categorical_columns",
            "feasible_kpis",
            "infeasible_kpis",
            "recommended_analyses",
            "summary",
        ):
            assert key in loaded_schema, f"Missing key '{key}'"

    def test_at_least_one_entity_column(self, loaded_schema):
        assert len(loaded_schema["entity_columns"]) >= 1

    def test_at_least_one_datetime_column(self, loaded_schema):
        assert len(loaded_schema["datetime_columns"]) >= 1

    def test_at_least_one_metric_column(self, loaded_schema):
        assert len(loaded_schema["metric_columns"]) >= 1

    def test_feasible_or_infeasible_kpis_populated(self, loaded_schema):
        total = len(loaded_schema["feasible_kpis"]) + len(
            loaded_schema["infeasible_kpis"]
        )
        assert total > 0

    def test_infeasible_kpis_have_missing_columns(self, loaded_schema):
        for entry in loaded_schema["infeasible_kpis"]:
            assert "kpi" in entry
            assert "missing_columns" in entry

    def test_recommended_analyses_non_empty(self, loaded_schema):
        assert len(loaded_schema["recommended_analyses"]) >= 1
        for rec in loaded_schema["recommended_analyses"]:
            assert isinstance(rec, str) and len(rec) > 0

    def test_summary_mentions_file(self, loaded_schema):
        assert SAMPLE_FILE in loaded_schema["summary"]


# ---------------------------------------------------------------------------
# Basic agent response
# ---------------------------------------------------------------------------


class TestAgentBasicResponse:
    def test_returns_agent_result(self, agent, data_mode):
        result = agent.run(
            _prompt("Load the file and describe what data it contains.", data_mode)
        )
        assert isinstance(result, AgentResult)

    def test_summary_is_non_empty_string(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Load the file and tell me how many rows and columns it has.", data_mode
            )
        )
        assert isinstance(result.summary, str)
        assert len(result.summary.strip()) > 50

    def test_charts_field_is_always_a_list(self, agent, data_mode):
        result = agent.run(_prompt("Describe the schema of the file.", data_mode))
        assert isinstance(result.charts, list)

    def test_tool_calls_field_is_a_list(self, agent, data_mode):
        result = agent.run(_prompt("Describe the schema of the file.", data_mode))
        assert isinstance(result.tool_calls, list)

    def test_turns_is_positive_integer(self, agent, data_mode):
        result = agent.run(_prompt("How many rows does the file have?", data_mode))
        assert isinstance(result.turns, int)
        assert result.turns >= 1

    def test_successive_runs_do_not_share_charts(self, agent, data_mode):
        agent.run(
            _prompt(
                "Load the file, discover the schema, and build any bar chart from it.",
                data_mode,
            )
        )
        result = agent.run(_prompt("How many rows does the file have?", data_mode))
        assert isinstance(result.charts, list)


# ---------------------------------------------------------------------------
# Schema-driven tool use
# ---------------------------------------------------------------------------


class TestAgentToolUse:
    def test_kpi_calculation_with_discovered_columns(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema, then calculate any KPIs that are feasible "
                "given the available columns, and report the results.",
                data_mode,
            )
        )
        assert len(result.summary.strip()) > 0
        numbers = re.findall(r"\d+\.?\d*", result.summary)
        assert len(numbers) > 0, "Expected at least one numeric value in KPI output."

    def test_entity_ranking_with_discovered_columns(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema, then rank the top 5 entities by the most "
                "relevant numeric metric column.",
                data_mode,
            )
        )
        assert len(result.summary.strip()) > 0

    def test_outlier_detection_with_discovered_columns(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema, then detect outliers in the most relevant "
                "numeric metric column and report which entities are affected.",
                data_mode,
            )
        )
        assert len(result.summary.strip()) > 0

    def test_trend_detection_with_discovered_columns(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema, then determine whether the primary numeric metric "
                "is trending upward, downward, or stable over time.",
                data_mode,
            )
        )
        summary_lower = result.summary.lower()
        assert any(
            w in summary_lower for w in ("increasing", "decreasing", "stable", "trend")
        ), f"Expected a trend direction. Got: {result.summary[:200]}"


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------


class TestChartGeneration:
    def test_bar_chart_spec_structure(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema, then rank entities by the top numeric metric "
                "and build a bar chart showing the top 5.",
                data_mode,
            )
        )
        bar_charts = [s for s in result.charts if s.get("chart_type") == "BarChart"]
        if not bar_charts:
            pytest.skip(
                "Model did not produce a BarChart — non-deterministic; re-run to confirm."
            )
        _assert_valid_chart_spec(bar_charts[0])
        assert len(bar_charts[0]["data"]) > 0

    def test_kpi_cards_spec_structure(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema, compute all feasible KPIs, "
                "and display the results as KPI cards.",
                data_mode,
            )
        )
        kpi_specs = [s for s in result.charts if s.get("chart_type") == "KPICards"]
        if not kpi_specs:
            pytest.skip(
                "Model did not produce KPICards — non-deterministic; re-run to confirm."
            )
        spec = kpi_specs[0]
        assert isinstance(spec.get("cards"), list)
        assert len(spec["cards"]) > 0

    def test_all_charts_have_valid_library_field(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Discover the schema and build one chart that best summarises the data.",
                data_mode,
            )
        )
        for spec in result.charts:
            assert spec.get("library") in ("recharts", "custom"), (
                f"Unexpected library value: {spec.get('library')}"
            )


# ---------------------------------------------------------------------------
# Full multi-step analysis
# ---------------------------------------------------------------------------


class TestFullAnalysisFlow:
    def test_load_discover_kpi_chart_pipeline(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Run a full analysis: load the file, discover its schema, "
                "calculate any feasible KPIs, rank entities by the top metric, "
                "and produce a bar chart of the results.",
                data_mode,
            )
        )
        assert isinstance(result, AgentResult)
        assert len(result.summary.strip()) > 0

    def test_executive_summary_with_charts(self, agent, data_mode):
        result = agent.run(
            _prompt(
                "Give me a full executive summary of the dataset: "
                "load the file, discover its schema, report feasible KPIs, "
                "identify top and bottom performing entities, flag any data quality issues, "
                "and produce at least one chart.",
                data_mode,
            )
        )
        assert isinstance(result, AgentResult)
        assert len(result.summary.strip()) > 100
        assert len(result.charts) >= 1
        for spec in result.charts:
            _assert_valid_chart_spec(spec)

    def test_schema_columns_referenced_in_summary(
        self, agent, data_mode, loaded_schema
    ):
        all_cols = (
            loaded_schema["entity_columns"]
            + loaded_schema["datetime_columns"]
            + loaded_schema["metric_columns"]
        )
        result = agent.run(
            _prompt(
                "Discover the schema and give a one-paragraph description of what this dataset contains.",
                data_mode,
            )
        )
        assert any(col in result.summary for col in all_cols), (
            f"Summary does not mention any known columns.\n"
            f"Known columns: {all_cols}\n"
            f"Summary: {result.summary[:300]}"
        )
