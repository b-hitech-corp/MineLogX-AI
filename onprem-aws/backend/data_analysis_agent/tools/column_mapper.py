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
  "bedrock" (default) — uses native boto3 (invoke_model) with Claude on Amazon Bedrock.
  "ollama"            — uses the Ollama HTTP API (qwen3:8b on EC2).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from data_analysis_agent.agent.prompts import (
    build_column_mapping_prompt,
    build_direct_kpi_prompt,
)
from data_analysis_agent.config.kpi_formulas import (
    KPI_REGISTRY,
    get_all_required_variables,
)
from data_analysis_agent.config.settings import settings
from data_analysis_agent.tools.bedrock_client import invoke_claude

logger = logging.getLogger(__name__)

# Variable caps per backend.
# Qwen3:8b (small model) struggles with long prompts — keep it at 20.
# Claude Sonnet 4.6 has a 200K-token context window and handles all variables comfortably.
_MAX_VARS_OLLAMA = 20
_MAX_VARS_BEDROCK = 999  # effectively no cap


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
            body = invoke_claude(
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                model_id=settings.bedrock.model_id,
            )
            return body["content"][0]["text"].strip()
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

    prompt = build_column_mapping_prompt(cols_text, var_lines, json_template)

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

    prompt = build_direct_kpi_prompt(col_lines, kpi_lines, json_template)

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
