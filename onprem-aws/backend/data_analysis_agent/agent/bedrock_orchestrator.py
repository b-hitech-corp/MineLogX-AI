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
from data_analysis_agent.agent.tool_schemas import TOOL_SCHEMAS
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

        messages: list[dict] = [
            {"role": "user", "content": build_task_prompt(question)}
        ]
        tool_calls_log: list[dict] = []
        summary = ""
        turns = 0

        while turns < self.max_turns:
            body = invoke_claude(
                messages,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                max_tokens=settings.bedrock.max_tokens,
                model_id=settings.bedrock.model_id,
            )
            turns += 1
            stop_reason = body.get("stop_reason")
            content = body.get("content", [])

            if verbose:
                logger.info("Turn %d — stop_reason=%s", turns, stop_reason)

            if stop_reason == "end_turn":
                summary = next((b["text"] for b in content if "text" in b), "")
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
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block_id,
                            "content": json.dumps(result, default=str),
                        }
                    )

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason (e.g. max_tokens) — capture any text and stop.
                summary = next((b["text"] for b in content if "text" in b), "")
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
