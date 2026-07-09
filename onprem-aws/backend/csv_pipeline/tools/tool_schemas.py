"""
Tool schemas for the CSV Vectorization Pipeline — Stage 1 schema inspection.

Structured JSON tool definitions (Anthropic Messages / tool_choice format) kept
separate from prose prompts (see prompts.py) so each file stays single-purpose.

Consumed by csv_pipeline/tools/schema_inspector.py:
  - INSPECT_TOOL — forced tool_choice schema (describe_csv_structure)
"""

INSPECT_TOOL: dict = {
    "name": "describe_csv_structure",
    "description": (
        "Analyse the structure of a CSV file sample and produce a transformation "
        "recipe that normalises it into a clean, flat, consistently-typed table."
    ),
    "input_schema": {
        "type": "object",
        "required": ["column_classifications", "transformation_steps", "reasoning"],
        "properties": {
            "column_classifications": {
                "type": "array",
                "description": "One entry per column in the file.",
                "items": {
                    "type": "object",
                    "required": ["name", "role", "kpi_variable", "confidence"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Exact column name as it appears in the file.",
                        },
                        "role": {
                            "type": "string",
                            "enum": [
                                "entity",
                                "metric",
                                "datetime",
                                "categorical",
                                "segment_marker",
                                "metadata",
                                "unknown",
                            ],
                        },
                        "kpi_variable": {
                            "type": ["string", "null"],
                            "description": (
                                "If this column maps to a KPI input variable "
                                "(e.g. 'fuel_volume_l' -> 'fuel_litres'), provide the "
                                "variable name. Otherwise null."
                            ),
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                },
            },
            "transformation_steps": {
                "type": "array",
                "description": (
                    "Ordered list of pandas-executable operations needed to normalise "
                    "this file. Empty array means the file is already a clean flat table."
                ),
                "items": {
                    "type": "object",
                    "required": ["operation", "params"],
                    "properties": {
                        "operation": {"type": "string"},
                        "params": {"type": "object"},
                    },
                },
            },
            "has_structural_anomalies": {
                "type": "boolean",
                "description": "True if embedded headers, separator rows, or type breaks were observed.",
            },
            "anomaly_description": {
                "type": ["string", "null"],
            },
            "reasoning": {
                "type": "string",
                "description": "Explain what you observed that led to your decisions.",
            },
        },
    },
}
