# Quickstart

Set up the full dev environment in 6 steps — from clone to a live environment check.

---

## 1. Clone and install tooling

```bash
git clone git@github.com:b-hitech-corp/MineLogX-AI.git
cd MineLogX-AI
bash scripts/dev-setup.sh          # Windows: ./scripts/dev-setup.ps1
```

This creates the `.venv`, installs all dependencies via `uv`, and wires the pre-commit git hooks
(ruff, bandit, yamllint, cfn-lint). Idempotent — safe to re-run.

---

## 2. Configure AWS SSO (once per machine)

```bash
aws configure sso --profile minelogx-admin
# SSO start URL : https://d-9067e84741.awsapps.com/start
# SSO region    : us-east-1
# Account       : 586928288932 (MineLogX POC)
# Role          : AvahiAdminAccess
```

---

## 3. Log in (per session)

The SSO token expires after ~8 hours. Re-run each session:

```bash
aws sso login --profile minelogx-admin
```

---

## 4. Verify access

```bash
uv run fab env.list                # lists minelogx-dev (and others if present)
uv run fab env.endpoints dev       # prints live API URL + Amplify frontend URL
```

---

## 5. Check stack health

```bash
uv run fab opensearch.status dev   # doc counts for csv_telemetry_vecs + pdf_legal_vecs
uv run fab lambda.status           # runtime config for all Lambda functions (default: dev)
uv run fab env.health dev          # aggregate health: Lambda + AOSS + Step Functions + Bedrock
```

---

## 6. (Optional) Spin up your own ephemeral stack

```bash
uv run fab env.up dev-<yourname> --seed
# e.g.
uv run fab env.up dev-cesar --seed
```

This deploys a full isolated copy of the stack (CloudFormation stacks prefixed `minelogx-dev-cesar-*`)
and seeds the S3 buckets with demo data.

---

## Day-to-day deploy flows

| What changed | Command |
|---|---|
| Lambda handler code only (no new deps) | `uv run fab lambda.redeploy <api\|csv\|pdf> dev` |
| Lambda + new dependency | `uv run fab lambda.build-layer <csv\|pdf>` → `uv run fab env.up dev --skip-frontend` |
| CloudFormation / infra change | `uv run fab env.up dev --skip-frontend` |
| Frontend only | `uv run fab frontend.deploy dev` |
| Docs only | `uv run fab docs.deploy dev` |
| Full stack (infra + frontend + docs) | `uv run fab env.up dev` |
