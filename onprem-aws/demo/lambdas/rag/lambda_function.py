import json
import logging
import os

from rag_agent_EC2 import RAGAgent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

VECTOR_BUCKET = os.environ.get(
    "VECTOR_BUCKET", "bhitech-minelogx-poc-legislation-documents-vector"
)
INDEX_NAME = os.environ.get("INDEX_NAME", "bhitech-minelogx-chunkspdfs")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
LLM_ENDPOINT = os.environ.get(
    "LLM_ENDPOINT", "http://ec2-98-81-228-187.compute-1.amazonaws.com:11434"
)
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
EMBEDDINGS_ENDPOINT = os.environ.get(
    "EMBEDDINGS_ENDPOINT", "http://ec2-3-208-23-94.compute-1.amazonaws.com:11434"
)
EMBEDDINGS_MODEL = os.environ.get("EMBEDDINGS_MODEL", "mxbai-embed-large")

agent = RAGAgent(
    vector_bucket_name=VECTOR_BUCKET,
    index_name=INDEX_NAME,
    region=AWS_REGION,
    llm_endpoint=LLM_ENDPOINT,
    llm_model=LLM_MODEL,
    embeddings_endpoint=EMBEDDINGS_ENDPOINT,
    embeddings_model=EMBEDDINGS_MODEL,
    top_k=5,
    top_n=3,
)

CORS_HEADERS = {
    "Content-Type": "application/json",
}


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

    query = body.get("query", "").strip()
    if not query:
        return {
            "statusCode": 400,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Missing required field: 'query'."}),
        }

    logger.info("Query: %s", query)
    result = agent.chat(query)

    parsed = json.loads(result)
    status_code = 200 if parsed.get("success") else 500

    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": result,
    }
