# Fabric Task Reference

All commands use `uv run fab <namespace>.<task> [args]`.
Fabric reserves `-e` for `--echo` — always use the long `--engine` flag for the engine option.

---

## `env.*` — Environment lifecycle

```bash
uv run fab env.up   dev --seed              # deploy CFN + seed S3 from demo buckets
uv run fab env.up   dev                     # deploy without seeding
uv run fab env.up   dev --skip-frontend     # infra only, skip frontend rebuild
uv run fab env.plan dev                     # preview changes (CFN change set, no apply)
uv run fab env.down dev                     # destroy the environment
uv run fab env.list                         # list active CFN stacks and TF workspaces
uv run fab env.endpoints dev               # print live URLs (API, Amplify, OpenSearch)
uv run fab env.endpoints qa                 # same for another environment
uv run fab env.health dev                   # aggregate health check (Lambda + AOSS + StepFn + Bedrock)
uv run fab env.bootstrap                    # create the S3 bucket for CFN uploads (once per account)
```

Engine defaults to `cloudformation`. Override with `--engine terraform` (alias: `tf` / `cf`).

**Fixed** environments: `dev` / `qa` / `prod`.
**Ephemeral** per-developer: `dev-<name>` (e.g. `dev-cesar`) — isolated by CFN stack prefix.

> `env.up` auto-recovers stacks in `ROLLBACK_COMPLETE`: deletes and recreates them automatically.

---

## `lambda.*` — Pipeline invocation and operations

```bash
uv run fab lambda.invoke csv dev                          # trigger CSV pipeline (Step Functions)
uv run fab lambda.invoke csv dev --wait                   # trigger + block until complete
uv run fab lambda.invoke csv dev --file-path C1/foo.csv   # use a specific S3 key
uv run fab lambda.invoke pdf dev                          # invoke PDF Lambda with synthetic S3 event
uv run fab lambda.invoke pdf dev --async                  # fire-and-forget (InvocationType=Event)
uv run fab lambda.invoke-all csv dev --parallel           # process every S3 CSV in parallel
uv run fab lambda.invoke-all pdf dev --async              # queue every S3 PDF asynchronously
uv run fab lambda.pdf-async-status                        # CloudWatch Logs Insights PDF status table
uv run fab lambda.redeploy api dev                        # re-zip backend/ + update-function-code
uv run fab lambda.redeploy api dev --publish              # redeploy + publish version + update alias 'live'
uv run fab lambda.rollback api dev                        # list published versions
uv run fab lambda.rollback api dev --version 3            # point alias 'live' to version 3
uv run fab lambda.status                                  # runtime config for all Lambdas (default: dev)
uv run fab lambda.logs api dev --follow                   # tail CloudWatch logs
uv run fab lambda.set-env pdf dev --key K --value V       # update Lambda env var (non-destructive)
uv run fab lambda.build-layer csv                         # build the CSV deps layer (no Docker)
uv run fab lambda.build-layer pdf                         # build the PDF deps layer
uv run fab lambda.pull                                    # download deployed demo Lambda code
```

---

## `opensearch.*` — Collection and index status

```bash
uv run fab opensearch.status                              # collection status + doc counts (default: dev)
uv run fab opensearch.status qa                           # same for another environment
uv run fab opensearch.reindex dev                         # flush + re-ingest all S3 files
```

---

## `frontend.*` — Amplify deployment

```bash
uv run fab frontend.deploy dev                            # build React/Vite + push to Amplify
uv run fab frontend.deploy dev --skip-build               # re-deploy using an existing dist/
uv run fab frontend.validate dev                          # validate API GW routes, CORS, and URL drift
```

---

## `bedrock.*` — Model access

```bash
uv run fab bedrock.model-access                           # probe all project models (GRANTED/DENIED)
uv run fab bedrock.set-model api dev us.amazon.nova-pro-v1:0   # change a pipeline's model
```

Available pipeline aliases: `api`, `api-nova`, `api-deepseek`, `csv`, `csv-embed`, `pdf`, `pdf-haiku`, `pdf-embed`.

---

## `step-functions.*` — CSV pipeline history

```bash
uv run fab step-functions.history dev                     # last 10 Step Functions executions
uv run fab step-functions.history dev --n 20              # last 20
```

---

## `ollama.*` — Demo EC2 remote ops (demo only)

```bash
uv run fab ollama.health-check                            # check all Ollama instances
uv run fab ollama.restart-ollama                          # restart Ollama container on all instances
uv run fab ollama.pull-model --host qwen3 --model qwen3:8b
uv run fab ollama.logs --host gemma3
```

---

## Activity Logs

Fabric writes structured logs to `.fab-logs/` (git-ignored):

| File pattern | Written by |
|---|---|
| `invoke-csv-<env>-<ts>.log` | `lambda.invoke csv` / `lambda.invoke-all csv` |
| `invoke-pdf-<env>-<ts>.log` | `lambda.invoke pdf` / `lambda.invoke-all pdf` |
| `pdf-async-status-<env>-<ts>.log` | `lambda.pdf-async-status` |
| `opensearch-status-<env>-<ts>.log` | `opensearch.status` |
| `up-<env>-<ts>.log` | `env.up` (on failure only) |
| `frontend-deploy-<env>-<ts>.log` | `frontend.deploy` |
