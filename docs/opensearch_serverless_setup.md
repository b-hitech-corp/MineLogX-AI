# Amazon OpenSearch Serverless (AOSS) setup for the vectorization pipelines

## Short answer

Both vectorization pipelines need an **AOSS vector-search collection**, and the code is
designed so they **share ONE collection**. Grounded in the configs
([csv_pipeline/config/opensearch_settings.py](csv_pipeline/config/opensearch_settings.py),
[pdf_pipeline/config/pdf_pipeline_settings.py](pdf_pipeline/config/pdf_pipeline_settings.py)):

| | CSV pipeline | PDF pipeline |
|---|---|---|
| Endpoint env var | `OPENSEARCH_HOST` | `OPENSEARCH_HOST` (same) |
| Index | `minelogx-telemetry-v1` (`OPENSEARCH_INDEX`) | `pdf_legal_vecs` (`PDF_OPENSEARCH_INDEX`) |
| Vector | knn_vector, 1024-dim, NextGen auto-HNSW | knn_vector, 1024-dim, cosine/HNSW |
| Embedding | Cohere `cohere.embed-v4:0` (int8→float32) | Titan `amazon.titan-embed-text-v2:0` |
| Auth | SigV4, `service='aoss'` | SigV4, `service='aoss'` |

Key consequences:
- **One collection, two indexes.** Both pipelines point at the same endpoint; they only
  differ by index name. (A second collection is unnecessary and just doubles cost.)
- **You do NOT create the indexes by hand.** `ensure_index_exists()` /
  `_ensure_index()` in the ingestors create them on first write with the correct mapping.
  You provision only: the collection, its 3 policies, and IAM on the calling principals.
- **Region must be `us-east-1`** to match the config defaults and Bedrock model access
  (or override `AWS_REGION` everywhere consistently).

## Prerequisites
- The principals that will call AOSS: the SageMaker notebook role
  `BaseNotebookInstanceEc2InstanceRole` (for testing now) and, later, the two Lambda
  execution roles (CSV + PDF). Collect their role ARNs.
- Bedrock model access enabled in the region for BOTH embedding models:
  `cohere.embed-v4:0` and `amazon.titan-embed-text-v2:0` (separate from the Claude access
  already enabled).

## An AOSS collection = collection + 3 policies
AOSS has no per-cluster security; access is governed by three policy objects plus IAM:
1. **Encryption policy** — which KMS key encrypts the collection (AWS-owned key is fine for POC).
2. **Network policy** — how the endpoint is reachable. For a POC with a SageMaker notebook +
   Lambda, **public access with SigV4** is simplest and matches the code (HTTPS + AWSV4SignerAuth).
   VPC-only is the prod hardening path (adds a VPC endpoint; defer).
3. **Data access policy** — which principals may do which collection/index operations.
4. Plus an **IAM identity policy** on each principal granting `aoss:APIAccessAll`.
   (Data-plane access requires BOTH the data access policy AND the IAM policy.)

## Path A — AWS Console (recommended for first creation)
1. OpenSearch Service console → **Serverless** → **Collections** → **Create collection**.
   - Name: e.g. `minelogx-vectors`. **Type: Vector search**. Capacity: leave default
     (or set min OCUs to control cost — see Cost note).
2. The wizard prompts for an **encryption policy** — accept "Use AWS owned key" (or pick a CMK).
3. **Network access** — choose **Public**, access to **OpenSearch endpoint** (and Dashboards
   optional). This lets the SageMaker notebook + Lambdas reach it over SigV4.
4. **Data access policy** — add a rule:
   - Principals: the SageMaker role ARN (and Lambda role ARNs when ready).
   - Grant **Collection** permissions: Create/Describe + **Index** permissions:
     Create Index, Read/Write Document, Update/Describe Index (or "All index operations").
   - Apply to collection `minelogx-vectors` and index pattern `*` (both indexes live here).
5. Create. Wait until **Active**, then copy the **collection endpoint**
   (`<id>.us-east-1.aoss.amazonaws.com`) — this is `OPENSEARCH_HOST` (no `https://`, no trailing `/`).

## Path B — AWS CLI (reproducible; basis for IaC)
Run in order; AOSS requires the encryption policy to exist before the collection.
```bash
REGION=us-east-1
COLL=minelogx-vectors
ROLE_ARN=arn:aws:iam::<acct>:role/BaseNotebookInstanceEc2InstanceRole   # + Lambda roles later

aws opensearchserverless create-security-policy --name ${COLL}-enc --type encryption \
  --region $REGION --policy '{"Rules":[{"ResourceType":"collection","Resource":["collection/'$COLL'"]}],"AWSOwnedKey":true}'

aws opensearchserverless create-security-policy --name ${COLL}-net --type network \
  --region $REGION --policy '[{"Rules":[{"ResourceType":"collection","Resource":["collection/'$COLL'"]},{"ResourceType":"dashboard","Resource":["collection/'$COLL'"]}],"AllowFromPublic":true}]'

aws opensearchserverless create-access-policy --name ${COLL}-data --type data \
  --region $REGION --policy '[{"Rules":[{"ResourceType":"collection","Resource":["collection/'$COLL'"],"Permission":["aoss:CreateCollectionItems","aoss:DescribeCollectionItems"]},{"ResourceType":"index","Resource":["index/'$COLL'/*"],"Permission":["aoss:CreateIndex","aoss:DescribeIndex","aoss:ReadDocument","aoss:WriteDocument","aoss:UpdateIndex"]}],"Principal":["'$ROLE_ARN'"]}]'

aws opensearchserverless create-collection --name $COLL --type VECTORSEARCH --region $REGION
aws opensearchserverless batch-get-collection --names $COLL --region $REGION \
  --query 'collectionDetails[0].collectionEndpoint' --output text   # -> OPENSEARCH_HOST
```

## IAM identity policy (attach to SageMaker role + each Lambda role)
```json
{ "Effect": "Allow", "Action": "aoss:APIAccessAll",
  "Resource": "arn:aws:aoss:us-east-1:<acct>:collection/<collection-id>" }
```

## Environment variables (SageMaker kernel + Lambda configs)
- `OPENSEARCH_HOST=<id>.us-east-1.aoss.amazonaws.com`  (shared by both pipelines)
- `AWS_REGION=us-east-1`
- CSV: `OPENSEARCH_INDEX=minelogx-telemetry-v1` (default already)
- PDF: `PDF_OPENSEARCH_INDEX=pdf_legal_vecs` (default already)
- leave `OPENSEARCH_VERIFY_CERTS=true`

## Verification
1. Connectivity (no index needed) — uses the pipeline's own client builder:
   ```bash
   cd <repo-root>
   python -c "from csv_pipeline.tools.opensearch_ingestor import _build_aoss_client; \
print(_build_aoss_client().info()['version'])"
   ```
   A version dict back = endpoint + SigV4 + data-access policy all working.
2. End-to-end — run Stage 4 (full pipeline) on one CSV; the ingestor auto-creates
   `minelogx-telemetry-v1` and indexes documents:
   ```bash
   python -m csv_pipeline.tests.example_run_pipeline C1/production_kpi_daily.csv   # all 4 stages
   ```
   Then confirm doc count via the client (`client.count(index="minelogx-telemetry-v1")`).
3. Repeat for the PDF pipeline once it runs → it auto-creates `pdf_legal_vecs`.

## Cost note
AOSS bills per OCU (~$0.24/OCU-hr). A vector collection has a baseline minimum (set
min indexing/search OCUs in the capacity settings to cap POC spend). Use ONE shared
collection — not two — to avoid doubling that baseline.
