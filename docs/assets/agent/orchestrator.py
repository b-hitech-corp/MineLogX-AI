"""
orchestrator.py — Fleet Management Agent
Implements Anthropic's recommended agentic loop pattern:
  1. Send messages + tools to the API
  2. If response contains tool_use blocks, execute them
  3. Append tool results as tool_result messages
  4. Repeat until stop_reason == "end_turn" or max_turns reached

All numeric reasoning is delegated to the tool layer.
The LLM decides WHAT to compute and HOW to present results.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
from anthropic import AnthropicBedrock

from agent.prompts import SYSTEM_PROMPT, build_task_prompt
from config.settings import settings
from tools import csv_loader, kpi_engine, stats_analyzer, insight_extractor, chart_spec_builder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (passed to the Anthropic API)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "csv_loader__load_csv",
        "description": (
            "Fetch a CSV file from S3 (or local dev path), parse it, infer its schema, "
            "and return a structured description including column types, null rates, "
            "and a 3-row preview. ALWAYS call this before other tools for a new file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "S3 key relative to the configured prefix, e.g. 'vehicles_may.csv'",
                },
                "date_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names that should be parsed as datetime.",
                },
                "use_local_fallback": {
                    "type": "boolean",
                    "description": "Set true during development to read from local sample_data/.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "kpi_engine__available_kpis",
        "description": "Return the catalogue of available KPI formulas. Call this when the user asks 'what KPIs can you compute?'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "kpi_engine__calculate_kpi",
        "description": (
            "Calculate one or more KPIs from a loaded CSV file using pre-defined formulas. "
            "Supports optional grouping (e.g. per vehicle) and pandas query filters. "
            "Never compute KPI values yourself — always use this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Key used when load_csv() was called."},
                "kpi_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "KPI IDs e.g. ['fuel_efficiency','idle_rate']. Use ['*'] for all.",
                },
                "group_by": {"type": "string", "description": "Column to group results by, e.g. 'vehicle_id'."},
                "filter_expr": {"type": "string", "description": "Pandas query string, e.g. \"region == 'North'\"."},
            },
            "required": ["file_path", "kpi_names"],
        },
    },
    {
        "name": "stats_analyzer__describe_columns",
        "description": "Descriptive statistics (mean, std, percentiles, skewness) for numeric columns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Leave empty for all numeric columns."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "stats_analyzer__rank_entities",
        "description": "Rank fleet entities (vehicles, drivers, routes) by a metric column.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "metric_column": {"type": "string", "description": "Column to rank by."},
                "entity_column": {"type": "string", "description": "Column identifying the entity (e.g. 'vehicle_id')."},
                "top_n": {"type": "integer", "default": 10},
                "ascending": {"type": "boolean", "default": False, "description": "True = bottom performers first."},
                "agg_func": {"type": "string", "enum": ["mean", "sum", "max", "min", "count"], "default": "mean"},
            },
            "required": ["file_path", "metric_column", "entity_column"],
        },
    },
    {
        "name": "stats_analyzer__time_series_aggregation",
        "description": "Aggregate numeric columns over time (daily/weekly/monthly), optionally per group.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "date_column": {"type": "string"},
                "value_columns": {"type": "array", "items": {"type": "string"}},
                "freq": {"type": "string", "enum": ["D", "W", "ME", "QE"], "default": "W"},
                "agg_func": {"type": "string", "enum": ["sum", "mean", "max", "min"], "default": "sum"},
                "group_by": {"type": "string", "description": "Column to split series by."},
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
                "columns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "insight_extractor__detect_outliers",
        "description": "Detect statistical outliers in a numeric column using IQR or Z-score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "column": {"type": "string"},
                "method": {"type": "string", "enum": ["iqr", "zscore"], "default": "iqr"},
                "threshold": {"type": "number", "default": 1.5},
                "entity_column": {"type": "string", "description": "Include entity IDs in results."},
            },
            "required": ["file_path", "column"],
        },
    },
    {
        "name": "insight_extractor__detect_trend",
        "description": "Fit a linear trend to a time-aggregated series and classify it as improving/declining/stable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "date_column": {"type": "string"},
                "value_column": {"type": "string"},
                "freq": {"type": "string", "enum": ["D", "W", "ME"], "default": "W"},
            },
            "required": ["file_path", "date_column", "value_column"],
        },
    },
    {
        "name": "insight_extractor__check_thresholds",
        "description": "Check rule-based thresholds (e.g. idle_rate > 30%) and return breaching rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "operator": {"type": "string", "enum": [">", "<", ">=", "<=", "=="]},
                            "value": {"type": "number"},
                            "label": {"type": "string"},
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
                "file_path": {"type": "string"},
                "metric_column": {"type": "string"},
                "entity_column": {"type": "string"},
                "top_n": {"type": "integer", "default": 5},
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
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}},
                "x_key": {"type": "string"},
                "y_keys": {"type": "array", "items": {"type": "string"}},
                "y_label": {"type": "string"},
                "x_label": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "data", "x_key", "y_keys"],
        },
    },
    {
        "name": "chart_spec_builder__build_bar_chart",
        "description": "Build a Recharts-compatible JSON spec for a bar chart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}},
                "x_key": {"type": "string"},
                "y_keys": {"type": "array", "items": {"type": "string"}},
                "layout": {"type": "string", "enum": ["vertical", "horizontal"], "default": "vertical"},
                "stacked": {"type": "boolean", "default": False},
                "y_label": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "data", "x_key", "y_keys"],
        },
    },
    {
        "name": "chart_spec_builder__build_kpi_cards",
        "description": "Build a KPI summary card layout spec.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "kpis": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {},
                            "unit": {"type": "string"},
                            "trend": {"type": "string"},
                        },
                    },
                },
                "description": {"type": "string"},
            },
            "required": ["title", "kpis"],
        },
    },
    {
        "name": "chart_spec_builder__build_pie_chart",
        "description": "Build a Recharts-compatible JSON spec for a pie/donut chart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}},
                "name_key": {"type": "string", "default": "name"},
                "value_key": {"type": "string", "default": "value"},
                "donut": {"type": "boolean", "default": True},
                "description": {"type": "string"},
            },
            "required": ["title", "data"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _dispatch(tool_name: str, tool_input: dict) -> Any:
    """Map tool_name → Python function and call it."""
    dispatch_map = {
        "csv_loader__load_csv":                   csv_loader.load_csv,
        "kpi_engine__available_kpis":             kpi_engine.available_kpis,
        "kpi_engine__calculate_kpi":              kpi_engine.calculate_kpi,
        "stats_analyzer__describe_columns":       stats_analyzer.describe_columns,
        "stats_analyzer__rank_entities":          stats_analyzer.rank_entities,
        "stats_analyzer__time_series_aggregation": stats_analyzer.time_series_aggregation,
        "stats_analyzer__correlation_matrix":     stats_analyzer.correlation_matrix,
        "insight_extractor__detect_outliers":     insight_extractor.detect_outliers,
        "insight_extractor__detect_trend":        insight_extractor.detect_trend,
        "insight_extractor__check_thresholds":    insight_extractor.check_thresholds,
        "insight_extractor__fleet_performance_summary": insight_extractor.fleet_performance_summary,
        "chart_spec_builder__build_line_chart":   chart_spec_builder.build_line_chart,
        "chart_spec_builder__build_bar_chart":    chart_spec_builder.build_bar_chart,
        "chart_spec_builder__build_kpi_cards":    chart_spec_builder.build_kpi_cards,
        "chart_spec_builder__build_pie_chart":    chart_spec_builder.build_pie_chart,
    }

    fn = dispatch_map.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        return fn(**tool_input)
    except TypeError as exc:
        return {"error": f"Tool call failed (wrong arguments): {exc}"}
    except Exception as exc:
        logger.exception("Tool %s raised an exception", tool_name)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Agent result
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    summary: str
    charts: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)   # audit trail
    turns: int = 0
    raw_messages: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class FleetAgent:
    def __init__(self) -> None:
        self.client = AnthropicBedrock()       # uses boto3 credential chain — no API key needed
        self.model = settings.bedrock.model_id
        self.max_tokens = settings.anthropic.max_tokens
        self.max_turns = settings.anthropic.max_agent_turns

    def run(self, question: str, *, verbose: bool = False) -> AgentResult:
        """
        Run the agentic loop for a user question.

        Parameters
        ----------
        question : str   The user's analytics question.
        verbose  : bool  Log tool calls to stdout.

        Returns
        -------
        AgentResult
        """
        messages: list[dict] = [
            {"role": "user", "content": build_task_prompt(question)}
        ]

        tool_calls_log: list[dict] = []
        charts: list[dict] = []
        turns = 0

        while turns < self.max_turns:
            turns += 1

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            if verbose:
                logger.info("Turn %d — stop_reason: %s", turns, response.stop_reason)

            # Append assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            # If the model is done, extract final answer
            if response.stop_reason == "end_turn":
                final_text = _extract_text(response.content)
                return AgentResult(
                    summary=final_text,
                    charts=charts,
                    tool_calls=tool_calls_log,
                    turns=turns,
                    raw_messages=messages,
                )

            # Process tool use blocks
            if response.stop_reason != "tool_use":
                # Unexpected stop reason — return what we have
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_id = block.id

                if verbose:
                    logger.info("  → %s(%s)", tool_name, json.dumps(tool_input)[:120])

                result = _dispatch(tool_name, tool_input)

                # Collect chart specs for structured output
                if tool_name.startswith("chart_spec_builder__") and isinstance(result, dict):
                    charts.append(result)

                # Log for audit trail
                tool_calls_log.append({
                    "turn": turns,
                    "tool": tool_name,
                    "input": tool_input,
                    "result_keys": list(result.keys()) if isinstance(result, dict) else type(result).__name__,
                })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps(result, default=str),
                })

            messages.append({"role": "user", "content": tool_results})

        # Max turns reached — return last text seen
        last_text = ""
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                last_text = _extract_text(msg["content"])
                if last_text:
                    break

        return AgentResult(
            summary=last_text or "[Agent reached max turn limit without completing.]",
            charts=charts,
            tool_calls=tool_calls_log,
            turns=turns,
            raw_messages=messages,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)
