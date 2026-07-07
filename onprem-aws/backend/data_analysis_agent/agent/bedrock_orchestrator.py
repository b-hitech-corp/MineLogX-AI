"""
bedrock_orchestrator.py — Fleet Management Agent (native boto3 / Amazon Bedrock)

Uses Claude Sonnet 4.6 via Bedrock (invoke_model, called through the shared
bedrock_client.invoke_claude helper) instead of the Ollama-based orchestrator.
The tool-use loop is implemented directly: each turn sends the current message
history to Claude, executes any requested tool calls, appends the results,
and repeats until the model reaches end_turn or max_turns is hit.

Native boto3 rather than the anthropic SDK: this agent is Claude-only and already
speaks Anthropic's Messages/tool format, so invoke_model accepts the same payload
the SDK was sending — only the transport changed, not the schemas or control flow.

Usage
-----
    from data_analysis_agent.agent.bedrock_orchestrator import FleetAgent

    agent = FleetAgent()
    result = agent.run("What is the average fuel efficiency across all vehicles?")
    print(result.summary)
    print(result.charts)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from data_analysis_agent.agent.prompts import SYSTEM_PROMPT, build_task_prompt
from data_analysis_agent.config.settings import settings
from data_analysis_agent.tools import (
    chart_spec_builder,
    csv_loader,
    insight_extractor,
    kpi_engine,
    schema_advisor,
    stats_analyzer,
)
from data_analysis_agent.tools.bedrock_client import invoke_claude

logger = logging.getLogger(__name__)

_run_charts: list[dict] = []


# ---------------------------------------------------------------------------
# Tool schemas — Anthropic tool format
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "csv_loader__load_csv",
        "description": (
            "Fetch a CSV file from S3 (or local dev path), parse it, infer its schema, "
            "and return a structured description including column types, null rates, and a "
            "3-row preview. ALWAYS call this before other tools for a new file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "S3 key or local file path."},
                "date_columns": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Column names to parse as dates.",
                },
                "use_local_fallback": {
                    "type": "boolean",
                    "description": "Use local sample_data/ folder instead of S3.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "schema_advisor__discover_schema",
        "description": (
            "Analyse the schema of a loaded CSV and return a structured analytics capability "
            "map: entity columns, datetime columns, metric columns, feasible KPIs, timestamp "
            "pairs, and recommended next-step analyses. Call this immediately after "
            "csv_loader__load_csv for every new file. Use its output to ground all subsequent "
            "tool calls — never reference column names that are not listed in the result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "kpi_engine__available_kpis",
        "description": "Return the catalogue of available KPI formulas. Call this when the user asks what KPIs can be computed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kpi_engine__calculate_kpi",
        "description": (
            "Calculate one or more KPIs from a loaded CSV file using pre-defined formulas. "
            "Supports optional grouping (e.g. per vehicle) and pandas query filters. "
            "Never compute KPI values yourself — always use this tool. "
            "Use kpi_names=['*'] to compute all available KPIs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "kpi_names": {
                    "type": "array", "items": {"type": "string"},
                    "description": "KPI names from registry, or ['*'] for all.",
                },
                "group_by": {"type": "string", "description": "Column name to compute KPIs per group."},
                "filter_expr": {"type": "string", "description": "Pandas query string applied before computing."},
            },
            "required": ["file_path", "kpi_names"],
        },
    },
    {
        "name": "stats_analyzer__describe_columns",
        "description": "Descriptive statistics (mean, std, percentiles, skewness) for numeric columns. Leave columns empty for all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "columns": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Specific columns to describe. Omit for all.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "stats_analyzer__rank_entities",
        "description": "Rank fleet entities (vehicles, drivers, routes) by a metric column. agg_func options: mean, sum, max, min, count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":     {"type": "string"},
                "metric_column": {"type": "string"},
                "entity_column": {"type": "string"},
                "top_n":         {"type": "integer"},
                "ascending":     {"type": "boolean"},
                "agg_func":      {"type": "string", "enum": ["mean", "sum", "max", "min", "count"]},
            },
            "required": ["file_path", "metric_column", "entity_column"],
        },
    },
    {
        "name": "stats_analyzer__time_series_aggregation",
        "description": (
            "Aggregate numeric columns over time. "
            "freq options: D (daily), W (weekly), ME (monthly), QE (quarterly). "
            "Optionally split by a group_by column."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":     {"type": "string"},
                "date_column":   {"type": "string"},
                "value_columns": {"type": "array", "items": {"type": "string"}},
                "freq":          {"type": "string", "enum": ["D", "W", "ME", "QE"]},
                "agg_func":      {"type": "string"},
                "group_by":      {"type": "string"},
            },
            "required": ["file_path", "date_column", "value_columns"],
        },
    },
    {
        "name": "stats_analyzer__correlation_matrix",
        "description": "Pearson correlation matrix for numeric columns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "columns":   {"type": "array", "items": {"type": "string"}},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "insight_extractor__detect_outliers",
        "description": (
            "Detect statistical outliers in a numeric column. "
            "method options: iqr (default), zscore. threshold is IQR multiplier or Z-score cutoff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":     {"type": "string"},
                "column":        {"type": "string"},
                "method":        {"type": "string", "enum": ["iqr", "zscore"]},
                "threshold":     {"type": "number"},
                "entity_column": {"type": "string"},
            },
            "required": ["file_path", "column"],
        },
    },
    {
        "name": "insight_extractor__detect_trend",
        "description": "Fit a linear trend to a time-aggregated series and classify it as improving, declining, or stable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":    {"type": "string"},
                "date_column":  {"type": "string"},
                "value_column": {"type": "string"},
                "freq":         {"type": "string"},
            },
            "required": ["file_path", "date_column", "value_column"],
        },
    },
    {
        "name": "insight_extractor__check_thresholds",
        "description": (
            "Check rule-based thresholds and return breaching rows. "
            "Each rule: {column, operator (>, <, >=, <=, ==), value, label (optional)}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column":   {"type": "string"},
                            "operator": {"type": "string"},
                            "value":    {"type": "number"},
                            "label":    {"type": "string"},
                        },
                        "required": ["column", "operator", "value"],
                    },
                },
            },
            "required": ["file_path", "rules"],
        },
    },
    {
        "name": "insight_extractor__fleet_performance_summary",
        "description": "Return top and bottom N performers for a metric. Good for executive summaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":     {"type": "string"},
                "metric_column": {"type": "string"},
                "entity_column": {"type": "string"},
                "top_n":         {"type": "integer"},
            },
            "required": ["file_path", "metric_column", "entity_column"],
        },
    },
    {
        "name": "chart_spec_builder__build_line_chart",
        "description": "Build a Recharts-compatible JSON spec for a line/time-series chart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string"},
                "data":        {"type": "array", "items": {"type": "object"}},
                "x_key":       {"type": "string"},
                "y_keys":      {"type": "array", "items": {"type": "string"}},
                "y_label":     {"type": "string"},
                "x_label":     {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "data", "x_key", "y_keys"],
        },
    },
    {
        "name": "chart_spec_builder__build_bar_chart",
        "description": "Build a Recharts-compatible JSON spec for a bar chart. layout options: vertical, horizontal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string"},
                "data":        {"type": "array", "items": {"type": "object"}},
                "x_key":       {"type": "string"},
                "y_keys":      {"type": "array", "items": {"type": "string"}},
                "layout":      {"type": "string", "enum": ["vertical", "horizontal"]},
                "stacked":     {"type": "boolean"},
                "y_label":     {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "data", "x_key", "y_keys"],
        },
    },
    {
        "name": "chart_spec_builder__build_kpi_cards",
        "description": "Build a KPI summary card layout spec. Each kpi: {label, value, unit, trend}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string"},
                "kpis":        {"type": "array", "items": {"type": "object"}},
                "description": {"type": "string"},
            },
            "required": ["title", "kpis"],
        },
    },
    {
        "name": "chart_spec_builder__build_pie_chart",
        "description": "Build a Recharts-compatible JSON spec for a pie or donut chart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string"},
                "data":        {"type": "array", "items": {"type": "object"}},
                "name_key":    {"type": "string"},
                "value_key":   {"type": "string"},
                "donut":       {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["title", "data"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _dispatch(name: str, inputs: dict) -> Any:
    """Route a model tool call to the correct Python function."""
    if name == "csv_loader__load_csv":
        return csv_loader.load_csv(**inputs)
    if name == "schema_advisor__discover_schema":
        return schema_advisor.discover_schema(**inputs)
    if name == "kpi_engine__available_kpis":
        return kpi_engine.available_kpis()
    if name == "kpi_engine__calculate_kpi":
        return kpi_engine.calculate_kpi(**inputs)
    if name == "stats_analyzer__describe_columns":
        return stats_analyzer.describe_columns(**inputs)
    if name == "stats_analyzer__rank_entities":
        return stats_analyzer.rank_entities(**inputs)
    if name == "stats_analyzer__time_series_aggregation":
        return stats_analyzer.time_series_aggregation(**inputs)
    if name == "stats_analyzer__correlation_matrix":
        return stats_analyzer.correlation_matrix(**inputs)
    if name == "insight_extractor__detect_outliers":
        return insight_extractor.detect_outliers(**inputs)
    if name == "insight_extractor__detect_trend":
        return insight_extractor.detect_trend(**inputs)
    if name == "insight_extractor__check_thresholds":
        return insight_extractor.check_thresholds(**inputs)
    if name == "insight_extractor__fleet_performance_summary":
        return insight_extractor.fleet_performance_summary(**inputs)
    if name == "chart_spec_builder__build_line_chart":
        spec = chart_spec_builder.build_line_chart(**inputs)
        _run_charts.append(spec)
        return spec
    if name == "chart_spec_builder__build_bar_chart":
        spec = chart_spec_builder.build_bar_chart(**inputs)
        _run_charts.append(spec)
        return spec
    if name == "chart_spec_builder__build_kpi_cards":
        spec = chart_spec_builder.build_kpi_cards(**inputs)
        _run_charts.append(spec)
        return spec
    if name == "chart_spec_builder__build_pie_chart":
        spec = chart_spec_builder.build_pie_chart(**inputs)
        _run_charts.append(spec)
        return spec
    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Agent result
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    summary: str
    charts: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    turns: int = 0
    raw_messages: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class FleetAgent:
    """
    Fleet analytics agent backed by Claude Sonnet 4.6 on Amazon Bedrock.

    Authentication uses the standard AWS credential chain (environment
    variables, ~/.aws/credentials, or an IAM role attached to the instance).
    No credentials need to be passed explicitly.
    """

    def __init__(self) -> None:
        self.max_turns = settings.bedrock.max_agent_turns

    def run(self, question: str, *, verbose: bool = False) -> AgentResult:
        """
        Run the agentic tool-use loop for a user question.

        Parameters
        ----------
        question : str   The user's analytics question.
        verbose  : bool  Log each turn and tool call to stdout.

        Returns
        -------
        AgentResult with the final summary, collected charts, and tool call log.
        """
        global _run_charts
        _run_charts = []

        if verbose:
            logger.info(
                "FleetAgent starting — model=%s region=%s",
                settings.bedrock.model_id,
                settings.bedrock.region,
            )

        messages: list[dict] = [{"role": "user", "content": build_task_prompt(question)}]
        tool_calls_log: list[dict] = []
        summary = ""
        turns = 0

        while turns < self.max_turns:
            body = invoke_claude(
                messages,
                system=SYSTEM_PROMPT,
                tools=_TOOL_SCHEMAS,
                max_tokens=settings.bedrock.max_tokens,
                model_id=settings.bedrock.model_id,
            )
            turns += 1
            stop_reason = body.get("stop_reason")
            content     = body.get("content", [])

            if verbose:
                logger.info("Turn %d — stop_reason=%s", turns, stop_reason)

            if stop_reason == "end_turn":
                summary = next(
                    (b["text"] for b in content if "text" in b), ""
                )
                break

            if stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": content})

                tool_results = []
                for block in content:
                    if block.get("type") != "tool_use":
                        continue
                    name, inputs, block_id = block["name"], block["input"], block["id"]
                    if verbose:
                        logger.info("  → %s(%s)", name, list(inputs.keys()))
                    try:
                        result = _dispatch(name, inputs)
                    except Exception as exc:
                        result = {"error": str(exc)}
                        logger.warning("Tool %s raised: %s", name, exc)
                    tool_calls_log.append({"tool": name, "input": inputs})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block_id,
                        "content": json.dumps(result, default=str),
                    })

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason (e.g. max_tokens) — capture any text and stop.
                summary = next(
                    (b["text"] for b in content if "text" in b), ""
                )
                logger.warning(
                    "Unexpected stop_reason=%s at turn %d", stop_reason, turns
                )
                break

        return AgentResult(
            summary=summary,
            charts=list(_run_charts),
            tool_calls=tool_calls_log,
            turns=turns,
            raw_messages=messages,
        )
