"""
bedrock_client.py — shared native boto3 Bedrock client for Claude in this unit.

schema_inspector.py previously called Claude through the third-party `anthropic`
SDK (`AnthropicBedrock`). That SDK already used AWS Bedrock under the hood — this
module just drops the wrapper and speaks Bedrock's `invoke_model` directly with
boto3, sending the same Anthropic Messages JSON body the SDK sent.

Why invoke_model (not Converse): this unit is Claude-only and already builds
Anthropic-format tool schemas (`input_schema`) with forced tool_choice.
invoke_model accepts that payload verbatim, so the migration is a transport
swap, not a schema translation.

This is an intentional, self-contained duplicate of
data_analysis_agent/tools/bedrock_client.py — the project's invariant is that no
unit imports another unit, so each unit keeps its own copy of shared-shaped
helpers (mirrors how config/settings.py is duplicated per unit).

Public API
----------
    invoke_claude(messages, system=None, tools=None, tool_choice=None,
                  max_tokens=None, model_id=None) -> dict
        Returns the parsed Anthropic Messages response body — same shape as
        anthropic.AnthropicBedrock().messages.create() would return, but as a
        plain dict (`body["stop_reason"]`, `body["content"]`, ...) instead of
        SDK objects.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from csv_pipeline.config.settings import settings

_ANTHROPIC_VERSION = "bedrock-2023-05-31"

_client = None


def _bedrock_rt():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=settings.bedrock.region)
    return _client


def invoke_claude(
    messages: list[dict],
    *,
    system: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    max_tokens: int | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Call Claude on Bedrock via invoke_model with the Anthropic Messages body.

    Parameters mirror anthropic.AnthropicBedrock().messages.create(): `system`,
    `tools`, and `tool_choice` are omitted from the body when not provided (plain
    completions don't need them; forced tool-use passes tool_choice, e.g.
    {"type": "tool", "name": "..."}). Returns the parsed response body dict.
    """
    body: dict[str, Any] = {
        "anthropic_version": _ANTHROPIC_VERSION,
        "max_tokens": max_tokens or settings.bedrock.max_tokens,
        "messages": messages,
    }
    if system is not None:
        body["system"] = system
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    response = _bedrock_rt().invoke_model(
        modelId=model_id or settings.bedrock.model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())
