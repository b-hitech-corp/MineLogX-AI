# Running the analysis vectorization (fill `analysis_vecs`)

This populates the **`analysis_vecs`** OpenSearch index with the data-analysis
agent's computed KPIs / outliers / trends / rankings, **per client**, so the RAG
chatbot can answer questions about the numbers shown in the UI
(e.g. *"What are the fuel efficiency KPIs for fleet C1?"*).

It is **ledger-gated**: a client that's already been ingested and hasn't changed
is skipped, and a re-ingest deletes the client's prior docs before writing — so
re-running never duplicates data.

> The chatbot's analysis retrieval is already wired, but it returns nothing until
> this has been run at least once for a client. Run it at **onboarding** and again
> whenever that client's CSVs in the telemetry bucket change.

---

## Prerequisites

- **AWS credentials** for the target account (the dev account for now), e.g.
  `export AWS_PROFILE=minelogx-admin`. The principal needs: `aoss` read/write on
  the collection, `s3` read on the telemetry bucket + read/write on the ledger
  prefix, and `bedrock:InvokeModel` for Claude (report computation) and Cohere
  (embeddings).
- **Python 3.11** with the backend deps:
  ```bash
  cd onprem-aws/backend
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```
- Run all commands **from `onprem-aws/backend/`** so the `data_analysis_agent`
  package is importable.

## Environment variables

| Var | Set to (dev) | Notes |
|---|---|---|
| `OPENSEARCH_HOST` | the dev AOSS collection endpoint (no `https://`) | e.g. `abc123.us-east-1.aoss.amazonaws.com` — from `fab env.endpoints dev` / CFN outputs |
| `FLEET_S3_BUCKET` | `minelogx-dev-telemetry-data` | telemetry **source** bucket **and** default ledger bucket |
| `AWS_REGION` | `us-east-1` | default |
| `ANALYSIS_INDEX` | *(leave unset)* | defaults to `analysis_vecs`, matching the API Lambda's CFN env |

```bash
export AWS_PROFILE=minelogx-admin
export AWS_REGION=us-east-1
export OPENSEARCH_HOST=<dev-aoss-endpoint>
export FLEET_S3_BUCKET=minelogx-dev-telemetry-data
```

## Commands

```bash
# One client
python -m data_analysis_agent.agent.ingest_orchestrator --client C1

# All clients discovered under the telemetry bucket (onboarding / backfill)
python -m data_analysis_agent.agent.ingest_orchestrator --all

# A specific subset
python -m data_analysis_agent.agent.ingest_orchestrator --all --clients C1,C2

# Force a re-ingest even if the ledger says the client is up to date
python -m data_analysis_agent.agent.ingest_orchestrator --client C1 --force

# Dry run against local sample_data/ (no S3, no ETag change-detection)
python -m data_analysis_agent.agent.ingest_orchestrator --client C1 --local
```

## What you'll see

A JSON array, one entry per client, e.g.:

```json
[
  {"client_id": "C1", "action": "indexed", "indexed": 48, "deleted": 40, "failed": 0, "errors": []}
]
```

- `action`: `indexed` (wrote docs) · `skipped` (ledger says unchanged) ·
  `no_files` (nothing in the bucket) · `error` (see `errors`).
- Exit code is non-zero if any client errored (handy for CI/scripts).

## Verify

- `fab opensearch.status dev` — collection health + per-index doc counts.
  (If `analysis_vecs` isn't listed there yet, it just needs adding to that task's
  index list — a small fabfile tweak; the docs still exist and are queryable.)
- Or ask the chatbot in the UI as **C1**: *"What are the fuel efficiency KPIs for
  fleet C1?"* — it should answer with the exact computed values.

## Idempotency / the ledger

- The control log lives at
  `s3://<FLEET_S3_BUCKET>/logs/analysis-ingest/ledger.jsonl` — one record per
  processed source file (client, source_file, S3 ETag, model, timestamp,
  doc_count, status).
- A client is re-ingested only if a source file is **new** or its **ETag changed**
  (or `ANALYSIS_PIPELINE_VERSION` was bumped). Otherwise it's skipped.
- `--force` ignores the ledger and re-ingests regardless.

## Optional: wrap it as a Fabric task

If you'd prefer `fab analysis.ingest`, a thin wrapper works — but note the Fabric
`uv` venv only has `fabric`/`boto3`/`tabulate`, not the analysis-agent deps, so the
task should **shell out** rather than import the orchestrator in-process, e.g.:

```python
@task
def ingest(c, env, client=None, all_=False, force=False):
    """analysis.ingest — vectorize data-analysis results into OpenSearch."""
    _ensure_aws(c)
    target = "--all" if all_ else f"--client {client}"
    extra = " --force" if force else ""
    c.run(
        f"cd onprem-aws/backend && "
        f"OPENSEARCH_HOST=$(...resolve for {env}...) "
        f"FLEET_S3_BUCKET={NAME_PREFIX}-{env}-telemetry-data "
        f"uv run --with-requirements requirements.txt "
        f"python -m data_analysis_agent.agent.ingest_orchestrator {target}{extra}"
    )
```

(Left out of this change on purpose — the Fabric/venv wiring is the backend's
call. The CLI above is the supported entrypoint either way.)
