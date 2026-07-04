import json
import logging

from agent.pipeline import FolderPipeline

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CORS_HEADERS = {
    "Content-Type": "application/json",
}

pipeline = FolderPipeline()

VALID_COMPANIES = {"C1", "C2", "C3"}


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    raw_body = event.get("body")
    try:
        body = json.loads(raw_body) if raw_body is not None else event
    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Request body is not valid JSON."}),
        }

    company = body.get("company", "").strip().upper()
    if company not in VALID_COMPANIES:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps(
                {"error": "Invalid 'company'. Valid values: C1, C2, C3."}
            ),
        }

    logger.info("Running pipeline on folder: %s", company)
    report = pipeline.run(company)

    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps(report, default=str),
    }
