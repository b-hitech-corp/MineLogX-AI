# MineLogX-AI — Bedrock Agent Improvement Plan

**Date:** 2026-06-12  
**Based on:** KPI Analysis Report C1 (5 pipeline runs), code review of `column_mapper.py`, `pipeline.py`, `bedrock_orchestrator.py`, `schema_advisor.py`, and official documentation for the Anthropic SDK, Strands Agents SDK, and Amazon Bedrock.

---

## 1. Diagnosis — Why the Bedrock Pipeline Underperforms

The report shows a clear pattern: the single Qwen3/Strands run (Report 2) produced 9 valid KPIs while 4 Claude runs produced between 3 and 5. The cause is **not model intelligence** — Claude Sonnet 4.6 is a significantly more capable model than Qwen3:8b. The cause is **architectural**.

### 1.1 The fundamental architectural gap

The Qwen3/Strands pipeline is **agentic**: it runs a tool-use loop. When a KPI calculation fails, the agent sees the error, reasons about an alternative column, and tries again. It has a feedback loop.

The current Bedrock pipeline is **single-pass**: it calls the column mapper once, takes whatever it returns, and runs KPI formulas. If the mapping is wrong, there is no retry, no error inspection, and no recovery.

```
Current Bedrock pipeline
────────────────────────
load_csv → column_mapper (one LLM call) → kpi_engine → DONE
                ↑                              ↓
           No feedback              Errors are logged but ignored
```

```
Strands / Qwen3 pipeline
────────────────────────
load_csv → agent observes schema
         → agent calls kpi_engine
         → KPI fails? agent sees error
         → agent retries with different column
         → converges on correct result
```

### 1.2 Specific failures traced to code

| Failure | Root cause in code |
|---|---|
| `scheduled_hours` / `operating_hours` never found | `_MAX_VARS = 20` cap (designed for Qwen3, not Claude) cut these variables from the prompt entirely |
| Same failure after raising cap to 999 | Sending 50+ variables in one unstructured prompt confuses even Claude — too much ambiguity, no grounding |
| `total_tonnes_moved` = 1.19 billion | Column mapper maps the wrong column for one file; no validation against statistics (mean = 170,011 t) |
| `tonnes_per_litre` = 80,699 | Wrong column mapped to `fuel_litres`; denominator is near-zero relative to numerator |
| Non-determinism run-to-run | Raw text generation with `temperature=0.0` is deterministic per prompt but the prompt content changes (variable ordering, etc.), producing different outputs |
| Safety KPIs absent in all Claude runs | Variables like `fatigue_events`, `unsafe_acts_count` were never included in the 20-variable cap |

### 1.3 Why the column mapper is the wrong tool for this job

`map_columns_to_kpi_variables` uses **raw text generation**: send a prompt, parse a JSON response. This pattern was appropriate for Qwen3 (which doesn't support native tool use) but is the wrong primitive for Claude.

Claude's API provides **tool use with `strict: true`** — the model is guaranteed to return a response that conforms to a JSON Schema. This eliminates the need for `_extract_json()` with its three fallback strategies, eliminates truncation risk, and makes the response deterministic in structure even if not in content.

> Source: Anthropic documentation — "Forcing tool use: `tool_choice: {"type": "tool", "name": "tool_name"}` combined with `strict: true` guarantees schema conformance." *(Implement Tool Use, Anthropic Docs)*

---

## 2. Two Architectural Proposals

### Option A — Strands + Claude Sonnet 4.6

**Concept:** Replace the current Bedrock pipeline with the same Strands agentic approach that already works, but powered by Claude instead of Qwen3. Strands defaults to Amazon Bedrock + Claude Sonnet 4, requiring minimal configuration change.

```python
# strands_fleet_agent.py
from strands import Agent, tool
from strands.models import BedrockModel

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6-20250514-v1:0",
    region_name="us-east-1",
)

@tool
def load_and_describe(file_path: str, use_local: bool = False) -> dict:
    """Load a CSV from S3 and return its schema with column types,
    null rates, and a statistical summary. Call this first for any new file."""
    ...

@tool
def calculate_kpis(file_path: str, kpi_names: list[str], ...) -> dict:
    """Calculate fleet KPIs using pre-defined formulas. Returns computed values
    and any column errors. Use kpi_names=['*'] for all feasible KPIs."""
    ...

agent = Agent(model=model, tools=[load_and_describe, calculate_kpis, ...])
result = agent("Analyse C1/fuel_management_events.csv and compute all fleet KPIs.")
```

**How the agentic loop solves the mapping problem:**

When `calculate_kpis` fails because `operating_hours` is not found, the agent sees the error in the tool result. It then calls `load_and_describe` again (or inspects the schema it already has), reasons that `MTBF` in the dataset corresponds to `operating_hours`, and retries the KPI with a `column_mapping` override. This is exactly what the Qwen3/Strands pipeline did — just with a better model.

**What changes from the existing `orchestrator.py`:**

The existing `orchestrator.py` already uses Strands with `@tool`. Switching to Claude requires only:
1. Change the model from `OllamaModel` to `BedrockModel`
2. Update the system prompt to be more directive about the mining analytics workflow
3. Add extended thinking for the planning/schema-reasoning steps

> Source: Strands Agents documentation — "The default model provider is Amazon Bedrock with Claude Sonnet 4. Credentials are resolved from the standard AWS credential chain." *(Strands Concepts: Agents)*

**Pros:**
- ✅ Proven approach — the exact pattern that already works with Qwen3
- ✅ Feedback loop: agent sees errors and retries intelligently
- ✅ Minimal new code — `orchestrator.py` is 90% there
- ✅ Strands handles conversation management, tool schema generation from type hints, and Bedrock auth
- ✅ Natural handling of missing columns (agent tries alternatives)
- ✅ Extended thinking available for complex multi-step reasoning

**Cons:**
- ⚠️ Non-deterministic execution order — harder to unit test, harder to explain to stakeholders
- ⚠️ Token cost is higher (multiple turns, longer context)
- ⚠️ Strands adds an external dependency
- ⚠️ Model can choose to skip steps or call tools in unexpected order

---

### Option B — Improved Anthropic SDK (Enhanced Deterministic Pipeline)

**Concept:** Keep the deterministic 10-step pipeline structure from `pipeline.py` but replace the fragile single-call column mapper with a structured, validated, retry-capable column resolution layer using Claude's native tool use API.

**The core change: tool-use column mapping**

Instead of asking Claude to output a JSON object as raw text (which can be malformed, truncated, or inconsistent), use the Anthropic SDK's `tool_choice` with a `map_column` tool. Claude is forced to call this tool for each variable — the response is guaranteed to conform to the schema.

```python
# New column resolution approach
def map_column_for_kpi(var_name: str, kpi_name: str, schema: dict) -> str | None:
    """
    Resolve a single KPI variable to a CSV column using Claude tool use.
    Uses tool_choice={"type":"tool"} so Claude MUST call map_column or return None.
    """
    tool = {
        "name": "map_column",
        "description": "Map one KPI variable to the best-matching CSV column.",
        "input_schema": {
            "type": "object",
            "properties": {
                "column_name": {
                    "type": ["string", "null"],
                    "description": "The CSV column name that best represents this variable, or null if no match exists."
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Confidence level of this mapping."
                },
                "reasoning": {
                    "type": "string",
                    "description": "One sentence explaining why this column matches."
                }
            },
            "required": ["column_name", "confidence", "reasoning"]
        }
    }
    
    col_info = _format_columns(schema)
    response = client.messages.create(
        model=settings.bedrock.model_id,
        max_tokens=256,
        tools=[tool],
        tool_choice={"type": "tool", "name": "map_column"},
        messages=[{
            "role": "user",
            "content": (
                f"KPI variable: `{var_name}` (used by KPI: {kpi_name})\n"
                f"CSV columns:\n{col_info}\n\n"
                "Which column best represents this variable? Be conservative — "
                "output null if no column clearly matches by meaning and units."
            )
        }]
    )
    result = response.content[0].input  # Guaranteed to match schema
    if result["confidence"] == "low":
        return None  # Reject uncertain mappings
    return result["column_name"]
```

**Then add a validation layer:**

After KPI computation, cross-check each result against the column's statistical mean:

```python
def _validate_kpi_value(kpi_name: str, computed_value: float, schema: dict) -> bool:
    """
    Sanity-check: if the KPI value is more than 3 orders of magnitude away
    from the mean of its source column, flag it as implausible.
    """
    # e.g. total_tonnes_moved = 1.19B vs column mean = 170,011 → flag
    ...
```

**And add a retry loop for failed KPIs:**

```python
def _compute_kpi_with_retry(kpi_name: str, file_path: str, schema: dict, 
                             max_retries: int = 2) -> dict | None:
    for attempt in range(max_retries + 1):
        column_mapping = map_column_for_kpi(kpi_name, ...)
        result = kpi_engine.calculate_kpi(file_path, [kpi_name], 
                                           column_mapping=column_mapping)
        if "error" not in result and _validate_kpi_value(...):
            return result
        # On failure, tell Claude what went wrong and ask it to reconsider
        ...
    return None
```

> Source: Anthropic documentation — "Use `tool_choice: {"type": "tool", "name": "tool_name"}` to force a specific tool call. The model MUST call that tool." *(Tool Use Overview, Anthropic Docs)*

> Source: Anthropic documentation — "The description is by far the most important factor in tool performance... be specific about when to use null vs a value." *(Implement Tool Use, Anthropic Docs)*

**Pros:**
- ✅ Deterministic execution order — same 10 steps every run
- ✅ Fully unit-testable — each step is a Python function
- ✅ Guaranteed schema conformance with `tool_choice` + `strict`
- ✅ Validation layer catches implausible values before they reach the dashboard
- ✅ No external framework dependency — pure Anthropic SDK
- ✅ Retry with error context gives Claude a second chance with feedback

**Cons:**
- ⚠️ More API calls (one per KPI variable vs one bulk call)
- ⚠️ Higher latency per file (sequential per-variable calls)
- ⚠️ More engineering work to build the retry + validation layer
- ⚠️ Still not truly agentic — errors in one step don't inform earlier steps

---

## 3. Comparison

| Dimension | Option A: Strands + Claude | Option B: SDK + Deterministic Pipeline |
|---|---|---|
| **KPI reliability** | High — agent retries on failure | Medium — retry layer helps but no cross-step feedback |
| **Determinism** | Low — execution order varies | High — same 10 steps every run |
| **Code complexity** | Low — Strands handles orchestration | High — retry + validation layer is significant new code |
| **Latency** | Medium — multi-turn but parallel tool calls | Medium-high — sequential per-variable API calls |
| **Token cost** | Higher — full conversation context | Lower — short focused calls |
| **Testability** | Harder — non-deterministic agent behavior | Easy — each function tested in isolation |
| **Debuggability** | Medium — Strands traces show tool calls | High — clear step-by-step log |
| **Extensibility** | High — add a tool, agent uses it | Medium — add a pipeline step |
| **Proven with this data** | ✅ Yes (Qwen3/Strands run = Report 2) | ❌ Not yet proven |
| **External dependency** | strands-agents | None (only anthropic SDK) |
| **Schema mapping quality** | Agent-driven, error-aware | Claude tool_use with structured output |
| **Extended thinking** | ✅ Available | ✅ Available (for mapping step) |

---

## 4. Recommendation

**Use Option A (Strands + Claude) as the primary analytics agent**, with Option B's validation layer added on top.

**Rationale:**

1. **It's already proven.** The Qwen3/Strands run is the only one to produce 9 correct KPIs across all 5 runs. The agentic loop is the differentiating factor, not the model. Replacing Qwen3 with Claude Sonnet 4.6 in the same Strands architecture is the lowest-risk path to a working Claude-based pipeline.

2. **The existing `orchestrator.py` is 90% there.** The tool definitions, dispatch function, and agent loop already exist. The change is replacing `OllamaModel` with `BedrockModel` and improving the system prompt.

3. **Column mapping errors are self-healing in an agentic loop.** Claude can reason: "The `calculate_kpi` call failed because `operating_hours` wasn't found. Looking at the schema, `MTBF` (mean=161.11) seems to represent hours between failures. Let me retry with that column." This is impossible in the deterministic pipeline without explicit retry code.

4. **Extended thinking for the planning step.** For the initial schema analysis — deciding which columns map to which KPI variables across all 47 KPIs — enable extended thinking with a budget of 5,000-8,000 tokens. This gives Claude a scratchpad to reason through ambiguous column names before committing to a mapping.

> Source: Anthropic extended thinking documentation — "Extended thinking is particularly valuable for complex multi-step reasoning and schema inference tasks." *(Extended Thinking, Anthropic Docs)*

**Option B's validation layer should be implemented regardless**, as a post-processing guard that catches implausible values (e.g., total_tonnes_moved > 10× the column mean). This is cheap to add and provides a safety net independent of the agent choice.

---

## 5. Implementation Roadmap

### Phase 1 — Fix the deterministic pipeline (1–2 days)
*Addresses the most critical bugs in the current Bedrock pipeline.*

- [ ] **Replace bulk column mapping with per-KPI tool-use calls** in `column_mapper.py`
  - Use `tool_choice: {"type": "tool"}` for guaranteed JSON schema conformance
  - Add `confidence` field — reject `"low"` confidence mappings
  - Group variables by KPI to reduce total API calls (one call per KPI, not per variable)

- [ ] **Add KPI value validation** in `pipeline.py`
  - After `kpi_engine.calculate_kpi()`, compare each result against the source column's statistical mean from the schema
  - Flag values that are >100× the column mean as implausible
  - Mark them as `"status": "implausible"` in the output rather than silently returning them

- [ ] **Fix the deduplication strategy** in `_build_dashboard`
  - Current "last wins" causes the 1.19B value to overwrite the 6,320 value
  - Instead: keep the value closest to the column's statistical mean

### Phase 2 — Strands + Claude agent (3–5 days)
*The target architecture. Builds on the existing `orchestrator.py`.*

- [ ] **Update `orchestrator.py`** to use `BedrockModel` with Claude Sonnet 4.6
  ```python
  from strands.models import BedrockModel
  model = BedrockModel(model_id=settings.bedrock.model_id, region_name=settings.bedrock.region)
  ```

- [ ] **Rewrite the system prompt** in `prompts.py` with explicit mining analytics workflow:
  - Step-by-step instructions: load → schema → mapping → calculate → validate
  - Domain-specific examples: `MTBF`, `scheduled_hours`, `fatigue_events`
  - Explicit instruction: "When a KPI fails due to missing column, inspect the schema statistics and retry with the column whose mean is closest to the expected KPI range"

- [ ] **Add extended thinking** for the schema analysis step
  ```python
  # In the system prompt or via a dedicated thinking call before the main agent loop
  thinking={"type": "enabled", "budget_tokens": 8000}
  ```

- [ ] **Add a `validate_kpi_result` tool** that the agent can call to cross-check values
  - Input: `kpi_name`, `computed_value`, `source_column`
  - Output: `{"valid": bool, "column_mean": float, "deviation_factor": float}`
  - Enables the agent to self-correct implausible values

- [ ] **Add retry guidance to tool descriptions**:
  > "If this call returns an error about a missing column, inspect the schema from the earlier `load_and_describe` call and retry with `column_mapping` set to the column whose name and mean value most closely match the KPI variable."

### Phase 3 — Observability and hardening (1–2 days)

- [ ] **Enable Strands OpenTelemetry integration** for per-run tracing
- [ ] **Log column mapping decisions** (which column was chosen, why, confidence)
- [ ] **Add regression tests** comparing computed KPI values against Report 2 (Qwen3) as ground truth
- [ ] **Add `pytest.ini` markers** for `bedrock` integration tests

---

## 6. Extended Thinking — Where to Use It

Extended thinking should be used **selectively**, not globally. It adds latency and token cost. The right places:

| Step | Use extended thinking? | Reason |
|---|---|---|
| Schema analysis (which columns → which KPI variables) | ✅ Yes | Ambiguous column names, 47 KPIs, needs multi-step reasoning |
| KPI retry after failure | ✅ Yes | Agent needs to reason about statistics + column semantics |
| Simple tool calls (load_csv, describe_columns) | ❌ No | No reasoning needed |
| Chart building | ❌ No | Deterministic, no ambiguity |
| Report summarisation | Optional | Improves quality, not correctness |

> Source: Anthropic extended thinking documentation — "Set `display: 'omitted'` for faster streaming time-to-first-token. Thinking still happens server-side; you are not charged differently." *(Extended Thinking, Anthropic Docs)*

---

## 7. Key Files to Change

| File | Change | Priority |
|---|---|---|
| `agent/orchestrator.py` | Replace `OllamaModel` with `BedrockModel` | P1 |
| `agent/prompts.py` | Rewrite system prompt for mining analytics with retry guidance | P1 |
| `tools/column_mapper.py` | Replace text generation with `tool_choice` structured calls; add per-KPI grouping | P1 |
| `agent/pipeline.py` | Fix deduplication strategy; add KPI value validation step | P1 |
| `config/settings.py` | No changes needed — `BedrockConfig` already present | — |
| `tools/schema_advisor.py` | No changes needed — `backend` param already threaded | — |

---

## 8. What the Report Tells Us About Claude's Strengths

Even in its degraded state, Claude reliably computed `fuel_consumption_rate` (124.01 L/hr) across all 4 runs. This KPI has an unambiguous column name match (`fuel_consumption_rate`) requiring no inference. Claude excels at **precise, well-defined tasks**. The failures are all in **ambiguous inference tasks** (column mapping under uncertainty) where an agentic feedback loop is the right solution.

The single most impactful change is not a model upgrade or prompt tweak — it is **giving Claude the ability to see its own errors and try again**.
