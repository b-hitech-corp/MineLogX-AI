from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import asdict
from typing import Any

from pdf_pipeline.agent.pdf_vectorization_pipeline import logger, run_pipeline
from pdf_pipeline.config.pdf_pipeline_settings import PdfPipelineConfig


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------
def lambda_handler(event: dict, context: Any) -> dict:
    """AWS Lambda entrypoint for EventBridge S3 PutObject events.

    EventBridge rule must be configured to forward S3 PutObject events
    with detail-type "Object Created" from the target S3 bucket.

    Expected event structure:
        {
            "detail": {
                "bucket": {"name": "<bucket>"},
                "object": {"key": "<url-encoded-key>"}
            }
        }
    """
    try:
        detail = event.get("detail", {})
        bucket = detail.get("bucket", {}).get("name", "")
        raw_key = detail.get("object", {}).get("key", "")
        key = urllib.parse.unquote_plus(raw_key)

        if not bucket or not key:
            logger.error(
                "Invalid event: missing bucket or key. Event: %s", json.dumps(event)
            )
            return {"statusCode": 400, "body": "Invalid event structure"}

        if not key.lower().endswith(".pdf"):
            logger.info("Skipping non-PDF object: %s", key)
            return {"statusCode": 200, "body": "Not a PDF — skipped"}

        cfg = PdfPipelineConfig(
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            opensearch_host=os.environ.get("OPENSEARCH_HOST", ""),
            opensearch_index=os.environ.get("PDF_OPENSEARCH_INDEX", "pdf_legal_vecs"),
            artifact_bucket=os.environ.get("PDF_ARTIFACT_BUCKET", bucket),
        )

        deadline_ts = None
        remaining_ms_fn = getattr(context, "get_remaining_time_in_millis", None)
        if callable(remaining_ms_fn):
            deadline_ts = time.time() + remaining_ms_fn() / 1000.0

        result = run_pipeline(bucket=bucket, key=key, config=cfg, deadline_ts=deadline_ts)

        # Async/EventBridge invocations never see the return body — this is the
        # only way pdf_async_status (fabfile.py) can see sections_indexed/errors.
        logger.info("PDF_PIPELINE_RESULT %s", json.dumps(asdict(result), default=str))

        status_code = 200 if result.overall_success else 207
        return {
            "statusCode": status_code,
            "body": json.dumps(asdict(result), default=str),
        }

    except Exception as exc:
        logger.error("Lambda handler unhandled exception: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc)}),
        }
