"""
Frontend & API Layer Lambda — request router behind API Gateway.

STATUS: thin placeholder. This is the seam where the *demo* API Lambda code
(the live minelogx-* functions serving the current frontend) will be dropped in
as the base — see the `lambda.pull` task in the plan (downloads the deployed
code from AWS into this folder). Once that lands, this handler grows the real
routes and delegates to the target-architecture agents:

    - data_analysis_agent  (Amazon Bedrock Claude — telemetry KPIs / insights)
    - rag_agent            (Amazon Bedrock — compliance Q&A over OpenSearch)

Guardrail (GUARDRAIL_ID) must be applied at every AI touchpoint.

For now it answers health checks so the API layer is deployable end-to-end.
"""

from __future__ import annotations

import json
import os


def lambda_handler(event: dict, context) -> dict:
    path = (event or {}).get("rawPath") or (event or {}).get("path") or "/"

    if path.rstrip("/") in ("", "/health", "/healthz"):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "status": "ok",
                    "service": "minelogx-api",
                    "opensearch_host": os.environ.get("OPENSEARCH_HOST", ""),
                    "guardrail_id": os.environ.get("GUARDRAIL_ID", ""),
                }
            ),
        }

    # Real routes (chat / analysis / compliance) land with the demo code + agents.
    return {
        "statusCode": 501,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not implemented yet", "path": path}),
    }
