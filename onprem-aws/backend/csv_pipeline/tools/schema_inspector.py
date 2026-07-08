"""
schema_inspector.py — CSV Vectorization Pipeline, Stage 1
==========================================================
Streams the full CSV file, builds a compact LLM profile, and calls Claude
(native boto3 invoke_model, forced tool_choice) to produce a transformation
recipe and column classifications. The resulting schema_descriptor.json is
persisted to S3.

Native boto3 rather than the anthropic SDK: this module is Claude-only and
already speaks Anthropic's Messages/tool format, so invoke_model accepts the
same payload the SDK was sending — only the transport changed.

This module contains only the functionality needed by the CSV Vectorization
Pipeline. The broader schema discovery tool for the Data Analysis Agent lives
in data_analysis_agent/tools/schema_advisor.py.

Public API
----------
    inspect_schema_sampled(file_path, local_mode, backend) -> dict
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import boto3
import requests

from csv_pipeline.config.settings import settings
from csv_pipeline.tools.bedrock_client import invoke_claude
from csv_pipeline.tools.csv_sampler import (
    StreamProfile,
    build_llm_input,
    stream_and_profile,
)
from csv_pipeline.tools.schema_reconciler import reconcile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM tool definition + system prompt (Claude tool_choice)
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
fleet formulas (e.g. 'fuel_volume_l' -> 'fuel_litres'), provide the canonical variable
name. Otherwise null.

**transformation_steps** — Ordered operations to produce a clean flat table:
- skip_rows, set_header_row, combine_header_rows, transpose, pivot_segments, melt,
  filter_rows, rename_columns, fill_forward, drop_columns.

If the file is already a clean flat table, output an empty transformation_steps array.

**reasoning** — Explain what you observed that led to your decisions."""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _llm_complete(prompt: str, backend: str, max_tokens: int = 1024) -> str | None:
    if backend == "bedrock":
        try:
            body = invoke_claude(
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                model_id=settings.bedrock.model_id,
            )
            return body["content"][0]["text"].strip()
        except Exception as exc:
            logger.warning("schema_inspector (bedrock): LLM call failed (%s)", exc)
            return None

    if backend == "ollama":
        try:
            resp = requests.post(
                f"{settings.ollama.endpoint}/api/generate",
                json={
                    "model": settings.ollama.model,
                    "prompt": f"/no_think\n{prompt}",
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": max_tokens},
                },
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as exc:
            logger.warning("schema_inspector (ollama): LLM call failed (%s)", exc)
            return None

    raise ValueError(f"Unknown backend '{backend}'. Choose 'bedrock' or 'ollama'.")


def _empty_inspect_result(error: str) -> dict:
    return {
        "column_classifications": [],
        "transformation_steps": [],
        "has_structural_anomalies": False,
        "anomaly_description": None,
        "reasoning": f"LLM call failed: {error}",
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    return None


def inspect_schema_with_tool_use(llm_input: str, backend: str = "bedrock") -> dict:
    """Send the compact CSV profile to Claude with tool_choice for structured output."""
    user_message = (
        "Analyse this CSV file and call describe_csv_structure with your findings.\n\n"
        f"{llm_input}"
    )

    if backend == "bedrock":
        try:
            body = invoke_claude(
                [{"role": "user", "content": user_message}],
                system=_INSPECT_SYSTEM_PROMPT,
                tools=[_INSPECT_TOOL],
                tool_choice={"type": "tool", "name": "describe_csv_structure"},
                max_tokens=4096,
                model_id=settings.bedrock.model_id,
            )
            content = body.get("content", [])
            tool_block = next((b for b in content if b.get("type") == "tool_use"), None)
            if tool_block is None:
                raise RuntimeError(
                    f"No tool_use block in response (stop_reason={body.get('stop_reason')}). "
                    f"Content types: {[b.get('type') for b in content]}"
                )
            logger.info(
                "inspect_schema_with_tool_use: %d classifications, %d steps",
                len(tool_block["input"].get("column_classifications", [])),
                len(tool_block["input"].get("transformation_steps", [])),
            )
            return tool_block["input"]
        except Exception as exc:
            logger.error("inspect_schema_with_tool_use (bedrock) failed: %s", exc)
            return _empty_inspect_result(str(exc))

    if backend == "ollama":
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


# ---------------------------------------------------------------------------
# Public API — Stage 1 entrypoint
# ---------------------------------------------------------------------------


def inspect_schema_sampled(
    file_path: str,
    local_mode: bool = False,
    backend: str = "bedrock",
) -> dict:
    """Stage 1 of the CSV Vectorization Pipeline.

    Streams the full CSV file, computes per-column statistics without loading
    it into memory, then sends a single Claude call (tool_choice) to produce a
    transformation recipe and column classifications. Persists the result as
    schema_descriptor.json to S3.

    Args:
        file_path:  S3 key (e.g. "C1/fuel_management_events.csv") or local path
                    relative to sample_data/ when local_mode=True.
        local_mode: Read from sample_data/ instead of S3.
        backend:    LLM backend — "bedrock" (default) or "ollama".

    Returns:
        dict — the schema_descriptor (also persisted to S3 unless local_mode=True).
    """
    logger.info("[schema_inspector] inspect_schema_sampled: '%s'", file_path)

    profile: StreamProfile = stream_and_profile(file_path, local_mode=local_mode)
    llm_input = build_llm_input(profile)
    llm_result = inspect_schema_with_tool_use(llm_input, backend=backend)

    descriptor = {
        "file_path": file_path,
        "schema_version": "1.0",
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "row_count": profile.row_count,
        "column_count": profile.column_count,
        "column_names": profile.column_names,
        "column_stats": {
            col: {
                "inferred_type": s.inferred_type,
                "null_pct": s.null_pct,
                "min": s.min_val,
                "max": s.max_val,
                "mean": s.mean,
                "cardinality": s.cardinality,
                "cardinality_capped": s.cardinality_capped,
                "sample_values": s.sample_values,
            }
            for col, s in profile.column_stats.items()
        },
        "column_classifications": llm_result.get("column_classifications", []),
        "transformation_steps": llm_result.get("transformation_steps", []),
        "has_structural_anomalies": llm_result.get("has_structural_anomalies", False),
        "anomaly_description": llm_result.get("anomaly_description"),
        "anomaly_records": [
            {
                "row_index": r.row_index,
                "anomaly_type": r.anomaly_type,
                "detail": r.detail,
            }
            for r in profile.anomaly_records
        ],
        "reasoning": llm_result.get("reasoning", ""),
    }

    # Deterministic reconciliation against the canonical schema. This is the
    # per-file resolved schema: which canonical fields are present/absent, which
    # raw columns are unknown (flagged + excluded, never force-mapped), and which
    # are ambiguous (quarantined). Runs independently of the LLM call above.
    reconciliation = reconcile(profile.column_names)
    descriptor["canonical"] = reconciliation.to_dict()
    if reconciliation.unknown_columns:
        logger.info(
            "[schema_inspector] %d unknown column(s) flagged (excluded from canonical "
            "schema, processing continues): %s",
            len(reconciliation.unknown_columns),
            reconciliation.unknown_columns,
        )
    if reconciliation.ambiguous_columns:
        logger.warning(
            "[schema_inspector] %d ambiguous column(s) quarantined for review: %s",
            len(reconciliation.ambiguous_columns),
            list(reconciliation.ambiguous_columns),
        )

    if not local_mode:
        s3_key = _persist_schema_descriptor(descriptor, file_path)
        descriptor["schema_s3_key"] = s3_key
        logger.info(
            "[schema_inspector] Schema descriptor written → s3://%s/%s",
            settings.s3.bucket_name,
            s3_key,
        )

    return descriptor


def _persist_schema_descriptor(descriptor: dict, file_path: str) -> str:
    """Write schema_descriptor.json to S3. Returns the S3 key."""
    p = PurePosixPath(file_path)
    folder = str(p.parent)
    if folder in (".", ""):
        folder = "root"
    stem = p.stem
    s3_key = f"{settings.s3.prefix}vectorization/{folder}/schema/{stem}.schema.json"

    s3 = boto3.client("s3", region_name=settings.s3.region)
    s3.put_object(
        Bucket=settings.s3.bucket_name,
        Key=s3_key,
        Body=json.dumps(descriptor, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    return s3_key
