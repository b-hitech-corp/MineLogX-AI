"""
column_mapper — maps actual CSV column names to KPI variable names using the LLM.

Two mapping modes:
  1. map_columns_to_kpi_variables(schema, backend="bedrock")
       Detects raw input columns that should be renamed before a KPI formula
       runs. E.g. 'fuel_volume_l' → 'fuel_litres'.

  2. map_direct_kpi_columns(schema, backend="bedrock")
       Detects columns that ARE already a pre-computed KPI value and need no
       formula at all. E.g. a 'fuel_efficiency' column (km/L) maps directly
       to the 'fuel_efficiency' KPI — no calculation required.

Both functions accept a backend parameter:
  "bedrock" (default) — uses the Anthropic SDK with Claude on Amazon Bedrock.
  "ollama"            — uses the Ollama HTTP API (qwen3:8b on EC2).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic
import requests

from data_analysis_agent.config.kpi_formulas import (
    KPI_REGISTRY,
    get_all_required_variables,
)
from data_analysis_agent.config.settings import settings

logger = logging.getLogger(__name__)

# Variable caps per backend.
# Qwen3:8b (small model) struggles with long prompts — keep it at 20.
# Claude Sonnet 4.6 has a 200K-token context window and handles all variables comfortably.
_MAX_VARS_OLLAMA = 20
_MAX_VARS_BEDROCK = 999  # effectively no cap

_bedrock_client = anthropic.AnthropicBedrock(aws_region=settings.bedrock.region)


# ---------------------------------------------------------------------------
# Internal LLM helpers — one per backend
# ---------------------------------------------------------------------------


def _llm_complete(prompt: str, backend: str, max_tokens: int = 1024) -> str | None:
    """
    Send *prompt* to the chosen backend and return the raw text response.
    Returns None on any failure (caller logs and falls back gracefully).
    """
    if backend == "bedrock":
        try:
            resp = _bedrock_client.messages.create(
                model=settings.bedrock.model_id,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text.strip()
        except Exception as exc:
            logger.warning("column_mapper (bedrock): LLM call failed (%s)", exc)
            return None

    if backend == "ollama":
        try:
            resp = requests.post(
                f"{settings.ollama.endpoint}/api/generate",
                json={
                    "model": settings.ollama.model,
                    "prompt": f"/no_think\n{prompt}",
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.0, "num_predict": max_tokens},
                },
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as exc:
            logger.warning("column_mapper (ollama): LLM call failed (%s)", exc)
            return None

    raise ValueError(f"Unknown backend '{backend}'. Choose 'bedrock' or 'ollama'.")


def map_columns_to_kpi_variables(
    schema: dict,
    backend: str = "bedrock",
) -> dict[str, str | None]:
    """
    Ask the LLM to match actual CSV columns to the variable names expected
    by the KPI formulas.

    Parameters
    ----------
    schema  : dict   Output of csv_loader.load_csv() for the file.
    backend : str    "bedrock" (default) or "ollama".

    Returns
    -------
    dict mapping each KPI variable name → matched column name (or None).
    Example: {"distance_km": "odometer_km", "fuel_litres": "fuel_volume_l",
              "idle_hours": None, "engine_on_hours": None}
    """
    all_vars = get_all_required_variables()

    # Flatten to unique variables with their first-seen KPI as context
    variable_sources: dict[str, str] = {}
    for kpi_name, variables in all_vars.items():
        for var in variables:
            if var not in variable_sources:
                variable_sources[var] = kpi_name

    if not variable_sources:
        return {}

    columns = schema.get("columns", [])
    col_names = {col["name"] for col in columns}
    numeric_cols = {
        col["name"] for col in columns if col["type"] in ("float", "integer")
    }

    # Prioritise variables that have at least one plausible column type match.
    # KPI variables that end in common numeric suffixes are almost always floats.
    _numeric_suffixes = (
        "_hours",
        "_km",
        "_litres",
        "_l",
        "_pct",
        "_rate",
        "_count",
        "_tonnes",
        "_min",
        "_t",
        "_score",
        "_kg",
        "_grams",
    )
    numeric_vars = {
        v for v in variable_sources if any(v.endswith(s) for s in _numeric_suffixes)
    }
    other_vars = {v for v in variable_sources if v not in numeric_vars}

    # Cap variables per backend: small models need a short prompt; Claude handles all of them.
    max_vars = _MAX_VARS_OLLAMA if backend == "ollama" else _MAX_VARS_BEDROCK
    candidate_vars: dict[str, str] = {}
    if numeric_cols:
        for v in sorted(numeric_vars):  # sorted → deterministic selection
            if len(candidate_vars) >= max_vars:
                break
            candidate_vars[v] = variable_sources[v]
    for v in sorted(other_vars):  # sorted → deterministic selection
        if len(candidate_vars) >= max_vars:
            break
        candidate_vars[v] = variable_sources[v]

    if not candidate_vars:
        return {v: None for v in variable_sources}

    # ── Build the prompt ──────────────────────────────────────────────────────

    col_lines = []
    for col in columns:
        line = f"- {col['name']} ({col['type']})"
        if col["type"] in ("float", "integer") and col.get("mean") is not None:
            line += f", mean={col['mean']}"
        col_lines.append(line)
    cols_text = "\n".join(col_lines)

    var_lines = "\n".join(f"- {var}  [{kpi}]" for var, kpi in candidate_vars.items())

    json_template = json.dumps(
        {v: "column_name_or_null" for v in candidate_vars}, indent=2
    )

    prompt = (
        "You are a precise data column matcher. Output ONLY valid JSON, nothing else.\n\n"
        f"CSV columns available:\n{cols_text}\n\n"
        f"Map each variable below to the best-matching CSV column name (or null):\n{var_lines}\n\n"
        "Rules:\n"
        "- Use only column names listed above.\n"
        "- Use null when no column is a good match.\n"
        "- Each column may be assigned to at most one variable.\n"
        "- Match by meaning, not just name "
        "(e.g. 'fuel_volume_l' → 'fuel_litres', 'equipment_id' → 'vehicle_id').\n\n"
        f"Respond with ONLY this JSON structure (replace values):\n{json_template}"
    )

    # ── Call the LLM ──────────────────────────────────────────────────────────

    # Scale output budget to the number of variables: ~40 tokens per key/value pair.
    # Bedrock (all vars) needs up to 4096; Ollama (20 vars) is fine with 1024.
    response_tokens = max(1024, len(candidate_vars) * 40)
    raw = _llm_complete(prompt, backend, max_tokens=response_tokens)
    if raw is None:
        return {v: None for v in variable_sources}

    logger.debug("column_mapper (%s) raw response: %s", backend, raw[:500])
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw_mapping = _extract_json(clean)

    if raw_mapping is None:
        logger.warning(
            "column_mapper (%s): no JSON found for '%s'. Snippet: %s",
            backend,
            schema.get("file_path"),
            clean[:300],
        )
        return {v: None for v in variable_sources}

    # ── Validate and return ───────────────────────────────────────────────────

    result: dict[str, str | None] = {}
    for var in variable_sources:
        candidate = raw_mapping.get(var)
        if (
            candidate
            and isinstance(candidate, str)
            and candidate in col_names
            and candidate != var
        ):
            # Numeric KPI variables must only map to numeric columns.
            # This prevents the LLM from matching e.g. 'tonnes' → 'vehicle_id'.
            if (
                any(var.endswith(s) for s in _numeric_suffixes)
                and candidate not in numeric_cols
            ):
                result[var] = None
            else:
                result[var] = candidate
        else:
            result[var] = None

    matched = [f"{v}→{c}" for v, c in result.items() if c]
    logger.info(
        "column_mapper (%s): %d/%d variables matched for '%s': %s",
        backend,
        len(matched),
        len(variable_sources),
        schema.get("file_path"),
        ", ".join(matched) if matched else "none",
    )
    return result


def map_direct_kpi_columns(
    schema: dict,
    backend: str = "bedrock",
) -> dict[str, str]:
    """
    Ask the LLM which CSV columns already contain a pre-computed KPI value,
    making formula execution unnecessary for those KPIs.

    Parameters
    ----------
    schema  : dict   Output of csv_loader.load_csv() for the file.
    backend : str    "bedrock" (default) or "ollama".

    Returns
    -------
    dict mapping kpi_name → column_name for every direct match found.
    Example: {"fuel_efficiency": "fuel_efficiency",
              "fleet_availability": "equipment_availability_pct",
              "mean_time_between_failures": "MTBF",
              "idle_rate": "idle_time_pct"}
    """
    columns = schema.get("columns", [])
    numeric_cols = [c for c in columns if c["type"] in ("float", "integer")]
    if not numeric_cols:
        return {}

    col_names = {c["name"] for c in numeric_cols}

    # Compact KPI catalogue — one line per KPI
    kpi_lines = "\n".join(
        f"- {kpi.name}: {kpi.description} [{kpi.unit}]" for kpi in KPI_REGISTRY.values()
    )

    col_lines = "\n".join(f"- {c['name']} ({c['type']})" for c in numeric_cols)

    # Template: one entry per column; LLM fills in the matching KPI name or null
    json_template = json.dumps(
        {c["name"]: "kpi_name_or_null" for c in numeric_cols}, indent=2
    )

    prompt = (
        "You are a precise KPI identifier. Output ONLY valid JSON, nothing else.\n\n"
        f"CSV numeric columns:\n{col_lines}\n\n"
        f"KPI catalogue:\n{kpi_lines}\n\n"
        "For each CSV column, decide if it directly holds a pre-computed KPI value "
        "(no formula needed). Match by meaning, description, and units.\n"
        "Rules:\n"
        "- Output null when the column is a raw input variable, not a final KPI.\n"
        "- Output the exact KPI name (from the catalogue) when it matches.\n"
        "- A column named 'fuel_efficiency' containing km/L IS the 'fuel_efficiency' KPI.\n"
        "- A column named 'equipment_availability_pct' (%) IS the 'fleet_availability' KPI.\n"
        "- A column named 'MTBF' (hours) IS the 'mean_time_between_failures' KPI.\n\n"
        f"Respond with ONLY this JSON structure (replace values):\n{json_template}"
    )

    # ── Call the LLM ──────────────────────────────────────────────────────────

    raw = _llm_complete(prompt, backend, max_tokens=512)
    if raw is None:
        return {}

    logger.debug("map_direct_kpi_columns (%s) raw response: %s", backend, raw[:500])
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw_mapping = _extract_json(clean)

    if raw_mapping is None:
        logger.warning(
            "map_direct_kpi_columns (%s): no JSON found for '%s'. Snippet: %s",
            backend,
            schema.get("file_path"),
            clean[:300],
        )
        return {}

    # Invert column→kpi to kpi→column, validating both sides
    result: dict[str, str] = {}
    for col_name, kpi_name in raw_mapping.items():
        if (
            kpi_name
            and isinstance(kpi_name, str)
            and kpi_name in KPI_REGISTRY
            and col_name in col_names
        ):
            result[kpi_name] = col_name

    matched = [f"{k}←{c}" for k, c in result.items()]
    logger.info(
        "map_direct_kpi_columns (%s): %d direct KPI column(s) found for '%s': %s",
        backend,
        len(matched),
        schema.get("file_path"),
        ", ".join(matched) if matched else "none",
    )
    return result


# ---------------------------------------------------------------------------
# Stage 1 — Schema inspection via tool_choice (CSV Vectorization Pipeline)
# ---------------------------------------------------------------------------

_INSPECT_TOOL: dict = {
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
                            "description": (
                                "entity=ID/identifier, metric=numeric measurement, "
                                "datetime=temporal, categorical=low-cardinality label, "
                                "segment_marker=divides file into logical segments, "
                                "metadata=admin/system field, unknown=cannot determine."
                            ),
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
                    "this file. Empty array means the file is already a clean flat table. "
                    "Supported operations: skip_rows, set_header_row, combine_header_rows, "
                    "transpose, pivot_segments, melt, filter_rows, rename_columns, "
                    "fill_forward, drop_columns."
                ),
                "items": {
                    "type": "object",
                    "required": ["operation", "params"],
                    "properties": {
                        "operation": {
                            "type": "string",
                            "description": "Name of the normalisation operation.",
                        },
                        "params": {
                            "type": "object",
                            "description": "Operation-specific parameters.",
                        },
                    },
                },
            },
            "has_structural_anomalies": {
                "type": "boolean",
                "description": "True if embedded headers, separator rows, or type breaks were observed.",
            },
            "anomaly_description": {
                "type": ["string", "null"],
                "description": "Brief description of anomalies found, or null.",
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Explain what you observed in the statistics and sample rows "
                    "that led to your column classifications and transformation recipe."
                ),
            },
        },
    },
}

_INSPECT_SYSTEM_PROMPT = """You are a CSV structure analyst for mining fleet telemetry data.

You will receive:
1. Per-column statistics (type, range, null rate, sample values) computed from the full file via streaming.
2. A structural sample: the first rows, any anomalous rows (with their type and index), and the last rows.

Your task is to call describe_csv_structure to provide:

**column_classifications** — For each column determine its role:
- entity: vehicle/driver/equipment identifiers (e.g. truck_id, driver_code)
- metric: numeric measurements (e.g. fuel_consumption_rate, payload_tonnes)
- datetime: temporal columns (e.g. shift_date, event_timestamp)
- categorical: low-cardinality labels (e.g. shift_type, location_code, status)
- segment_marker: column whose distinct values divide the file into logical segments
- metadata: administrative/system fields (record_id, source_system, created_at)
- unknown: cannot determine from available evidence

For kpi_variable: if a column directly represents a KPI input variable used in mining
fleet formulas (e.g. 'fuel_volume_l' -> 'fuel_litres', 'equip_avail_pct' -> 'available_hours'),
provide the canonical variable name. Otherwise null.

**transformation_steps** — Ordered operations to produce a clean flat table:
- skip_rows: {"n": int} — remove leading metadata rows
- set_header_row: {"row": int} — promote a non-first row to be the header
- combine_header_rows: {"rows": [int, ...]} — merge multi-level headers into one
- transpose: {} — swap rows and columns
- pivot_segments: {"segment_col": str, "value_col": str} — long to wide on a segment column
- melt: {"id_vars": [...], "value_vars": [...]} — wide to long
- filter_rows: {"exclude_expr": str} — pandas query string to remove summary/total rows
- rename_columns: {"mapping": {"old_name": "new_name", ...}} — rename columns
- fill_forward: {"column": str} — forward-fill sparse segment headers
- drop_columns: {"names": [...]} — remove columns by name

If the file is already a clean flat table, output an empty transformation_steps array.

**reasoning** — Explain what you observed that led to your decisions. Be specific about
which rows or statistics informed each transformation step."""


def inspect_schema_with_tool_use(llm_input: str, backend: str = "bedrock") -> dict:
    """
    Send the compact CSV profile to Claude using tool_choice, receiving a
    guaranteed-schema-conformant transformation recipe and column classifications.

    Parameters
    ----------
    llm_input : str   Output of csv_sampler.build_llm_input().
    backend   : str   "bedrock" (default) or "ollama".

    Returns
    -------
    The tool input dict from Claude (keys: column_classifications,
    transformation_steps, has_structural_anomalies, anomaly_description, reasoning).
    On failure, returns a safe empty-recipe dict with error details.
    """
    user_message = (
        "Analyse this CSV file and call describe_csv_structure with your findings.\n\n"
        f"{llm_input}"
    )

    if backend == "bedrock":
        try:
            resp = _bedrock_client.messages.create(
                model=settings.bedrock.model_id,
                max_tokens=4096,
                system=_INSPECT_SYSTEM_PROMPT,
                tools=[_INSPECT_TOOL],
                tool_choice={"type": "tool", "name": "describe_csv_structure"},
                messages=[{"role": "user", "content": user_message}],
            )
            # tool_choice should guarantee a tool_use block, but guard defensively
            # in case Bedrock prepends a text block under throttling or refusal.
            tool_block = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_block is None:
                raise RuntimeError(
                    f"No tool_use block in response (stop_reason={resp.stop_reason}). "
                    f"Content types: {[b.type for b in resp.content]}"
                )
            logger.info(
                "inspect_schema_with_tool_use: %d classifications, %d steps",
                len(tool_block.input.get("column_classifications", [])),
                len(tool_block.input.get("transformation_steps", [])),
            )
            return tool_block.input
        except Exception as exc:
            logger.error("inspect_schema_with_tool_use (bedrock) failed: %s", exc)
            return _empty_inspect_result(str(exc))

    if backend == "ollama":
        # Ollama does not support tool_choice — fall back to text generation + parse
        logger.warning(
            "inspect_schema_with_tool_use: ollama does not support tool_choice; "
            "results may be less reliable."
        )
        raw = _llm_complete(
            f"{_INSPECT_SYSTEM_PROMPT}\n\n{user_message}\n\n"
            "Respond with ONLY a JSON object matching the describe_csv_structure schema.",
            backend="ollama",
            max_tokens=4096,
        )
        if raw is None:
            return _empty_inspect_result("ollama call returned None")
        clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        parsed = _extract_json(clean)
        if parsed is None:
            return _empty_inspect_result("could not parse JSON from ollama response")
        return parsed

    raise ValueError(f"Unknown backend '{backend}'. Choose 'bedrock' or 'ollama'.")


def _empty_inspect_result(error: str) -> dict:
    """Safe fallback when the LLM call fails — empty recipe, error captured."""
    return {
        "column_classifications": [],
        "transformation_steps": [],
        "has_structural_anomalies": False,
        "anomaly_description": None,
        "reasoning": f"LLM call failed: {error}",
    }


# ---------------------------------------------------------------------------
# (existing _extract_json below — unchanged)
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any] | None:
    """
    Try several strategies to pull a JSON object out of an LLM response.

    Handles:
    - Plain JSON:            {"key": "val"}
    - Markdown code block:  ```json\\n{...}\\n```
    - JSON with leading/trailing prose
    """
    if not text:
        return None

    # Strategy 1: JSON inside a markdown code block
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 2: find the outermost { ... } by bracket counting
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    # Strategy 3: try to parse the whole cleaned text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    return None
