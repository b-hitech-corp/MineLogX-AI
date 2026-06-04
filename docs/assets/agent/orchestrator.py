"""
orchestrator.py — Fleet Management Agent
Uses Strands Agents SDK with qwen3:8b running on an EC2 Ollama instance.

The Strands Agent handles the tool-use loop automatically.
Tools are declared with the @tool decorator; Strands generates their schemas
from type annotations and docstrings and passes them to the model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from strands import Agent, tool
from strands.models.ollama import OllamaModel

from agent.prompts import SYSTEM_PROMPT, build_task_prompt
from config.settings import settings
from tools import csv_loader, kpi_engine, stats_analyzer, insight_extractor, chart_spec_builder, schema_advisor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chart capture — chart tool wrappers append here; reset at each run()
# ---------------------------------------------------------------------------
_run_charts: list[dict] = []


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@tool
def csv_loader__load_csv(
    file_path: str,
    date_columns: Optional[list[str]] = None,
    use_local_fallback: bool = False,
) -> dict:
    """
    Fetch a CSV file from S3 (or local dev path), parse it, infer its schema,
    and return a structured description including column types, null rates,
    and a 3-row preview. ALWAYS call this before other tools for a new file.
    """
    return csv_loader.load_csv(
        file_path=file_path,
        date_columns=date_columns,
        use_local_fallback=use_local_fallback,
    )


@tool
def schema_advisor__discover_schema(file_path: str) -> dict:
    """
    Analyse the schema of a loaded CSV and return a structured analytics capability map:
    entity columns, datetime columns, metric columns, feasible KPIs, timestamp pairs,
    and recommended next-step analyses. Call this immediately after csv_loader__load_csv
    for every new file. Use its output to ground all subsequent tool calls — never
    reference column names that are not listed in the result.
    """
    return schema_advisor.discover_schema(file_path=file_path)


@tool
def kpi_engine__available_kpis() -> dict:
    """Return the catalogue of available KPI formulas. Call this when the user asks what KPIs can be computed."""
    return kpi_engine.available_kpis()


@tool
def kpi_engine__calculate_kpi(
    file_path: str,
    kpi_names: list[str],
    group_by: Optional[str] = None,
    filter_expr: Optional[str] = None,
) -> dict:
    """
    Calculate one or more KPIs from a loaded CSV file using pre-defined formulas.
    Supports optional grouping (e.g. per vehicle) and pandas query filters.
    Never compute KPI values yourself — always use this tool.
    Use kpi_names=['*'] to compute all available KPIs.
    """
    return kpi_engine.calculate_kpi(
        file_path=file_path,
        kpi_names=kpi_names,
        group_by=group_by,
        filter_expr=filter_expr,
    )


@tool
def stats_analyzer__describe_columns(
    file_path: str,
    columns: Optional[list[str]] = None,
) -> dict:
    """Descriptive statistics (mean, std, percentiles, skewness) for numeric columns. Leave columns empty for all."""
    return stats_analyzer.describe_columns(file_path=file_path, columns=columns)


@tool
def stats_analyzer__rank_entities(
    file_path: str,
    metric_column: str,
    entity_column: str,
    top_n: int = 10,
    ascending: bool = False,
    agg_func: str = "mean",
) -> dict:
    """
    Rank fleet entities (vehicles, drivers, routes) by a metric column.
    agg_func options: mean, sum, max, min, count.
    """
    return stats_analyzer.rank_entities(
        file_path=file_path,
        metric_column=metric_column,
        entity_column=entity_column,
        top_n=top_n,
        ascending=ascending,
        agg_func=agg_func,
    )


@tool
def stats_analyzer__time_series_aggregation(
    file_path: str,
    date_column: str,
    value_columns: list[str],
    freq: str = "W",
    agg_func: str = "sum",
    group_by: Optional[str] = None,
) -> dict:
    """
    Aggregate numeric columns over time. freq options: D (daily), W (weekly), ME (monthly), QE (quarterly).
    Optionally split by a group_by column.
    """
    return stats_analyzer.time_series_aggregation(
        file_path=file_path,
        date_column=date_column,
        value_columns=value_columns,
        freq=freq,
        agg_func=agg_func,
        group_by=group_by,
    )


@tool
def stats_analyzer__correlation_matrix(
    file_path: str,
    columns: Optional[list[str]] = None,
) -> dict:
    """Pearson correlation matrix for numeric columns."""
    return stats_analyzer.correlation_matrix(file_path=file_path, columns=columns)


@tool
def insight_extractor__detect_outliers(
    file_path: str,
    column: str,
    method: str = "iqr",
    threshold: float = 1.5,
    entity_column: Optional[str] = None,
) -> dict:
    """
    Detect statistical outliers in a numeric column.
    method options: iqr (default), zscore. threshold is IQR multiplier or Z-score cutoff.
    """
    return insight_extractor.detect_outliers(
        file_path=file_path,
        column=column,
        method=method,
        threshold=threshold,
        entity_column=entity_column,
    )


@tool
def insight_extractor__detect_trend(
    file_path: str,
    date_column: str,
    value_column: str,
    freq: str = "W",
) -> dict:
    """Fit a linear trend to a time-aggregated series and classify it as improving, declining, or stable."""
    return insight_extractor.detect_trend(
        file_path=file_path,
        date_column=date_column,
        value_column=value_column,
        freq=freq,
    )


@tool
def insight_extractor__check_thresholds(
    file_path: str,
    rules: list[dict],
) -> dict:
    """
    Check rule-based thresholds and return breaching rows.
    Each rule: {column, operator (>, <, >=, <=, ==), value, label (optional)}.
    """
    return insight_extractor.check_thresholds(file_path=file_path, rules=rules)


@tool
def insight_extractor__fleet_performance_summary(
    file_path: str,
    metric_column: str,
    entity_column: str,
    top_n: int = 5,
) -> dict:
    """Return top and bottom N performers for a metric. Good for executive summaries."""
    return insight_extractor.fleet_performance_summary(
        file_path=file_path,
        metric_column=metric_column,
        entity_column=entity_column,
        top_n=top_n,
    )


@tool
def chart_spec_builder__build_line_chart(
    title: str,
    data: list[dict],
    x_key: str,
    y_keys: list[str],
    y_label: Optional[str] = None,
    x_label: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Build a Recharts-compatible JSON spec for a line/time-series chart."""
    spec = chart_spec_builder.build_line_chart(
        title=title, data=data, x_key=x_key, y_keys=y_keys,
        y_label=y_label, x_label=x_label, description=description,
    )
    _run_charts.append(spec)
    return spec


@tool
def chart_spec_builder__build_bar_chart(
    title: str,
    data: list[dict],
    x_key: str,
    y_keys: list[str],
    layout: str = "vertical",
    stacked: bool = False,
    y_label: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Build a Recharts-compatible JSON spec for a bar chart. layout options: vertical, horizontal."""
    spec = chart_spec_builder.build_bar_chart(
        title=title, data=data, x_key=x_key, y_keys=y_keys,
        layout=layout, stacked=stacked, y_label=y_label, description=description,
    )
    _run_charts.append(spec)
    return spec


@tool
def chart_spec_builder__build_kpi_cards(
    title: str,
    kpis: list[dict],
    description: Optional[str] = None,
) -> dict:
    """Build a KPI summary card layout spec. Each kpi: {label, value, unit, trend}."""
    spec = chart_spec_builder.build_kpi_cards(title=title, kpis=kpis, description=description)
    _run_charts.append(spec)
    return spec


@tool
def chart_spec_builder__build_pie_chart(
    title: str,
    data: list[dict],
    name_key: str = "name",
    value_key: str = "value",
    donut: bool = True,
    description: Optional[str] = None,
) -> dict:
    """Build a Recharts-compatible JSON spec for a pie or donut chart."""
    spec = chart_spec_builder.build_pie_chart(
        title=title, data=data, name_key=name_key, value_key=value_key,
        donut=donut, description=description,
    )
    _run_charts.append(spec)
    return spec


_TOOLS = [
    csv_loader__load_csv,
    schema_advisor__discover_schema,
    kpi_engine__available_kpis,
    kpi_engine__calculate_kpi,
    stats_analyzer__describe_columns,
    stats_analyzer__rank_entities,
    stats_analyzer__time_series_aggregation,
    stats_analyzer__correlation_matrix,
    insight_extractor__detect_outliers,
    insight_extractor__detect_trend,
    insight_extractor__check_thresholds,
    insight_extractor__fleet_performance_summary,
    chart_spec_builder__build_line_chart,
    chart_spec_builder__build_bar_chart,
    chart_spec_builder__build_kpi_cards,
    chart_spec_builder__build_pie_chart,
]


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
    def __init__(self) -> None:
        self.model = OllamaModel(
            host=settings.ollama.endpoint,
            model_id=settings.ollama.model,
        )
        self.max_turns = settings.ollama.max_agent_turns

    def run(self, question: str, *, verbose: bool = False) -> AgentResult:
        """
        Run the agentic loop for a user question.

        Parameters
        ----------
        question : str   The user's analytics question.
        verbose  : bool  Log progress to stdout.

        Returns
        -------
        AgentResult
        """
        global _run_charts
        _run_charts = []

        if verbose:
            logger.info("Running agent with model %s on %s", settings.ollama.model, settings.ollama.endpoint)

        agent = Agent(
            model=self.model,
            tools=_TOOLS,
            system_prompt=SYSTEM_PROMPT,
        )

        response = agent(build_task_prompt(question))

        return AgentResult(
            summary=str(response),
            charts=list(_run_charts),
        )
