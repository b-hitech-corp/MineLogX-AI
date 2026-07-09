"""
Tool schemas for the Fleet Management Agent (native boto3 / Amazon Bedrock).

Structured JSON tool definitions (Anthropic Messages / tool-use format) kept
separate from prose prompts (see prompts.py) so each file stays single-purpose.

Consumed by agent/bedrock_orchestrator.py (passed as `tools=` to invoke_claude
and routed by its `_dispatch` function).

NOTE: The Strands/Ollama orchestrator (agent/orchestrator.py) declares the same
16 tools a different way — as @tool-decorated Python functions whose docstrings
serve as the tool descriptions — so it cannot import this module. When a tool's
schema or description changes here, mirror it in orchestrator.py's @tool
functions to keep the two orchestrators at parity.
"""

TOOL_SCHEMAS: list[dict] = [
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
                "file_path": {
                    "type": "string",
                    "description": "S3 key or local file path.",
                },
                "date_columns": {
                    "type": "array",
                    "items": {"type": "string"},
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
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "KPI names from registry, or ['*'] for all.",
                },
                "group_by": {
                    "type": "string",
                    "description": "Column name to compute KPIs per group.",
                },
                "filter_expr": {
                    "type": "string",
                    "description": "Pandas query string applied before computing.",
                },
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
                    "type": "array",
                    "items": {"type": "string"},
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
                "file_path": {"type": "string"},
                "metric_column": {"type": "string"},
                "entity_column": {"type": "string"},
                "top_n": {"type": "integer"},
                "ascending": {"type": "boolean"},
                "agg_func": {
                    "type": "string",
                    "enum": ["mean", "sum", "max", "min", "count"],
                },
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
                "file_path": {"type": "string"},
                "date_column": {"type": "string"},
                "value_columns": {"type": "array", "items": {"type": "string"}},
                "freq": {"type": "string", "enum": ["D", "W", "ME", "QE"]},
                "agg_func": {"type": "string"},
                "group_by": {"type": "string"},
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
        "description": (
            "Detect statistical outliers in a numeric column. "
            "method options: iqr (default), zscore. threshold is IQR multiplier or Z-score cutoff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "column": {"type": "string"},
                "method": {"type": "string", "enum": ["iqr", "zscore"]},
                "threshold": {"type": "number"},
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
                "file_path": {"type": "string"},
                "date_column": {"type": "string"},
                "value_column": {"type": "string"},
                "freq": {"type": "string"},
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
                            "column": {"type": "string"},
                            "operator": {"type": "string"},
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
                "top_n": {"type": "integer"},
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
        "description": "Build a Recharts-compatible JSON spec for a bar chart. layout options: vertical, horizontal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}},
                "x_key": {"type": "string"},
                "y_keys": {"type": "array", "items": {"type": "string"}},
                "layout": {"type": "string", "enum": ["vertical", "horizontal"]},
                "stacked": {"type": "boolean"},
                "y_label": {"type": "string"},
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
                "title": {"type": "string"},
                "kpis": {"type": "array", "items": {"type": "object"}},
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
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}},
                "name_key": {"type": "string"},
                "value_key": {"type": "string"},
                "donut": {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["title", "data"],
        },
    },
]
