"""
Lambda entrypoint for the CSV Vectorization Pipeline.

Thin wrapper over csv_pipeline.agent.csv_vectorization_pipeline.run_pipeline so
the pipeline can be driven by the Step Functions state machine
(minelogx-<env>-csv-pipeline), which invokes this handler once per stage group:

    Stage 1        SchemaInspection    stages=[1]
    Stages 2-3     NormalizeAndChunk   stages=[2, 3]
    Stage 4        OpenSearchIngest    stages=[4]

Event shape (state machine merges "stages" into the execution input):
    {
        "file_path": "C1/fuel_management_events.csv",   # required — S3 key
        "stages":    [1],                                # optional — default all
        "force":     false                               # optional
    }

Environment variables (see config/settings.py + config/opensearch_settings.py):
    OPENSEARCH_HOST, OPENSEARCH_INDEX, FLEET_S3_BUCKET, FLEET_S3_PREFIX,
    BEDROCK_MODEL_ID, BEDROCK_EMBED_MODEL_ID.
"""

from __future__ import annotations

import logging

from csv_pipeline.agent.csv_vectorization_pipeline import run_pipeline

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    file_path = event.get("file_path")
    if not file_path:
        return {"statusCode": 400, "body": "Missing required 'file_path' in event"}

    stages = event.get("stages")  # None → run all stages
    force = bool(event.get("force", False))

    result = run_pipeline(file_path=file_path, stages=stages, force=force)

    # Pass file_path + a compact per-stage summary through so the state machine
    # can chain stages on the same input.
    return {
        "statusCode": 200 if result.overall_success else 207,
        "file_path": file_path,
        "overall_success": result.overall_success,
        "failed_stages": result.failed_stages,
        "stages": [
            {
                "stage": r.stage,
                "name": r.name,
                "status": r.status_label,
                "artifact_key": r.artifact_key,
                "errors": r.errors,
            }
            for r in result.stage_results
        ],
    }
