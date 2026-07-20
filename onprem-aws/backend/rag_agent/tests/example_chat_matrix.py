#!/usr/bin/env python3
"""
example_chat_matrix.py — live chat-endpoint matrix test (manual, not pytest)
=============================================================================
Sends 5 fixed queries x 3 clients x 3 models (45 calls total) to the live
MineLogX chat endpoint, and records the full response + metadata to a CSV.

Purpose: verify the client-scoped retrieval fix (PR #42) and compare model
behavior, across every (client, model) combination, using identical prompts
per client so answers are directly comparable.

Deliberately NOT named test_*.py / *_test.py: it makes 45 real, billed Bedrock
calls against the live dev endpoint, so it must never be auto-collected/run by
`pytest rag_agent/tests/` — same convention as pdf_pipeline/tests/example_run_pipeline.py.
Run it explicitly, by hand, only.

Known caveat (see conversation_turn/history_turns columns in the output):
the API Lambda's BedrockRAGAgent is a module-level singleton per warm
container — conversation history is NOT session-keyed by client, so a run
of many sequential calls can accumulate cross-client history in one warm
container. Retrieval itself remains correctly client-scoped regardless
(that's the part PR #42 fixed); this only affects the LLM's own memory of
prior turns. Captured here, not hidden.

Usage:
    python3 example_chat_matrix.py [--out results.csv] [--delay 1.0]

No third-party dependencies — stdlib only (urllib, json, csv).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

ENDPOINT = "https://tlq4tejl75.execute-api.us-east-1.amazonaws.com/dev/chat"

CLIENTS = ["C1", "C2", "C3"]

MODELS = ["claude-sonnet-4.6", "nova-pro", "deepseek-v3.2"]

# Same 5 queries for every client — identical prompts make cross-client and
# cross-model answers directly comparable. Chosen to span different report
# sections (fuel, maintenance, load/tonnage, safety, general KPIs/anomalies)
# so clients lacking a given section's data (e.g. C2 has no fuel telemetry)
# give a visible "no data" honesty signal rather than all queries hitting
# the same section.
QUERIES = [
    "What is the fuel efficiency for this fleet?",
    "What is the fleet's maintenance status and are there any predicted equipment failures?",
    "How much tonnage has been moved and what is the load efficiency?",
    "What safety incidents or fatigue-related risks have been detected?",
    "Summarize the key KPI anomalies or outliers detected for this fleet.",
]

REQUEST_TIMEOUT_S = 45
RETRY_ON_TIMEOUT = 1  # extra attempts after the first, on timeout/network error only

CSV_FIELDS = [
    "request_index",
    "sent_at",
    "client",
    "query_index",
    "query_text",
    "model_key",
    "model_id",
    "success",
    "http_status",
    "attempt_count",
    "error_message",
    "optimized_query",
    "answer",
    "answer_chars",
    "doc_count",
    "indexes_retrieved",
    "pdf_hits",
    "csv_hits",
    "analysis_hits",
    "generation_elapsed_s",
    "request_elapsed_s",
    "conversation_turn",
    "history_turns",
    "response_timestamp",
]


def send_query(client: str, model: str, query: str) -> tuple[dict, int, float]:
    """POST one query; returns (parsed_json_or_error_dict, http_status, elapsed_s).

    Retries once on timeout/connection error (not on a real HTTP error status,
    which is returned as-is so it's visible in the CSV rather than masked).
    """
    if urlparse(ENDPOINT).scheme != "https":
        # Scheme is validated before every urlopen (not just at import time) so
        # this stays safe if ENDPOINT is ever parameterized via env/CLI later —
        # urlopen() otherwise accepts file:// and other unexpected schemes.
        raise ValueError(f"Refusing to open a non-https URL: {ENDPOINT!r}")

    body = json.dumps({"query": query, "model": model, "client": client}).encode(
        "utf-8"
    )
    last_exc = None
    for attempt in range(1 + RETRY_ON_TIMEOUT):
        req = urllib.request.Request(
            ENDPOINT,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # nosec B310 -- scheme validated above
                elapsed = time.perf_counter() - t0
                raw = resp.read().decode("utf-8")
                return json.loads(raw), resp.status, elapsed
        except urllib.error.HTTPError as e:
            # Real HTTP error (4xx/5xx) — return it, don't retry (not transient).
            elapsed = time.perf_counter() - t0
            try:
                raw = e.read().decode("utf-8")
                parsed = json.loads(raw)
            except Exception:
                parsed = {"error": str(e)}
            return parsed, e.code, elapsed
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            elapsed = time.perf_counter() - t0
            if attempt < RETRY_ON_TIMEOUT:
                time.sleep(2.0)
                continue
            return {"error": f"{type(e).__name__}: {e}"}, 0, elapsed
    return {"error": f"unreachable; last_exc={last_exc}"}, 0, 0.0


def build_row(
    request_index: int,
    sent_at: str,
    client: str,
    query_index: int,
    query: str,
    model: str,
    response: dict,
    status: int,
    elapsed: float,
    attempt_count: int,
) -> dict:
    success = status == 200 and bool(response.get("success"))
    docs = response.get("retrieval", {}).get("documents", []) if success else []
    indexes = response.get("retrieval", {}).get("indexes", []) if success else []

    def count_hits(idx_name_fragment: str) -> int:
        return sum(1 for d in docs if idx_name_fragment in d.get("index", ""))

    return {
        "request_index": request_index,
        "sent_at": sent_at,
        "client": client,
        "query_index": query_index + 1,
        "query_text": query,
        "model_key": model,
        "model_id": response.get("model", {}).get("model_id", ""),
        "success": success,
        "http_status": status,
        "attempt_count": attempt_count,
        "error_message": "" if success else json.dumps(response)[:500],
        "optimized_query": response.get("query", {}).get("optimized", ""),
        "answer": response.get("answer", ""),
        "answer_chars": len(response.get("answer", "")),
        "doc_count": response.get("retrieval", {}).get("document_count", 0),
        "indexes_retrieved": ",".join(indexes),
        "pdf_hits": count_hits("pdf"),
        "csv_hits": count_hits("csv"),
        "analysis_hits": count_hits("analysis"),
        "generation_elapsed_s": response.get("performance", {}).get(
            "generation_elapsed_s", ""
        ),
        "request_elapsed_s": round(elapsed, 3),
        "conversation_turn": response.get("conversation", {}).get("turn", ""),
        "history_turns": response.get("conversation", {}).get("history_turns", ""),
        "response_timestamp": response.get("timestamp", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="chat_matrix_results.csv", help="Output CSV path")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = ap.parse_args()

    total = len(CLIENTS) * len(MODELS) * len(QUERIES)
    print(f"Endpoint: {ENDPOINT}")
    print(
        f"Plan: {len(CLIENTS)} clients x {len(MODELS)} models x {len(QUERIES)} queries "
        f"= {total} requests -> {args.out}\n"
    )

    rows: list[dict] = []
    request_index = 0
    failures = 0

    for client in CLIENTS:
        for model in MODELS:
            for qi, query in enumerate(QUERIES):
                request_index += 1
                sent_at = datetime.now(timezone.utc).isoformat()
                print(
                    f"[{request_index:2d}/{total}] client={client:3s} model={model:16s} "
                    f"q{qi + 1}: {query[:50]!r}...",
                    end=" ",
                    flush=True,
                )

                response, status, elapsed = send_query(client, model, query)
                row = build_row(
                    request_index,
                    sent_at,
                    client,
                    qi,
                    query,
                    model,
                    response,
                    status,
                    elapsed,
                    attempt_count=1,
                )
                rows.append(row)

                if row["success"]:
                    print(
                        f"OK  {elapsed:5.1f}s  docs={row['doc_count']} "
                        f"(pdf={row['pdf_hits']} csv={row['csv_hits']} "
                        f"analysis={row['analysis_hits']})"
                    )
                else:
                    failures += 1
                    print(f"FAIL status={status}  {row['error_message'][:80]}")

                time.sleep(args.delay)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {total - failures}/{total} succeeded, {failures} failed.")
    print(f"Results written to {args.out}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
