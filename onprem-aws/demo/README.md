# onprem-aws/demo

Artifacts of the **hand-deployed demo** (the live POC in AWS), kept separate from
the target-architecture application code in `../backend/`.

## lambdas/

Source code of the deployed demo Lambdas, downloaded by:

```bash
uv run fab lambda.pull            # → demo/lambdas/<fn>/ (ml, rag, ...)
```

This is **reference material** — the base we port into the target `api` handler
(`../backend/lambdas/api/handler.py`) as the Frontend & API Layer is built. It is
NOT deployed as-is by the IaC.

Each function folder holds the extracted package plus `._deployed-config.json`
(a snapshot of the live handler/runtime/memory/timeout/env vars/layers, gitignored).

**Commit policy:** commit only our own source (handlers + local modules). Vendored
runtime dependencies inside the zip (boto3, numpy, …) are flagged by `lambda.pull`
and kept out of git via `lambdas/.gitignore` — review before committing.

> The demo's infrastructure (VPC, EC2 Ollama, API Gateway, S3, IAM) is captured
> as Terraform in `../infrastructure/terraform/environments/_imported-demo/`. The
> demo Lambda **functions** themselves were left as deferred imports because their
> code wasn't in the repo — this folder is where that code now lives.
