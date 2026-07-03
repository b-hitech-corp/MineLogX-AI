# infrastructure/

Infrastructure-as-Code for MineLogX-AI. Following the **dual-tool strategy**
(see `CLAUDE.md` → *IaC Strategy*), the platform is defined in **both**
Terraform and CloudFormation, and orchestrated per-environment by **Fabric**
(`fabfile.py`, task namespace `env.*`).

```
infrastructure/
├── terraform/                  # Terraform definition (state owner of the imported demo)
│   ├── versions.tf             # Terraform + AWS provider version constraints
│   ├── backend.tf              # Remote state (S3 + DynamoDB lock) — bootstrap first
│   ├── modules/                # Reusable modules (vpc, s3, lambda, api_gateway, ...)
│   ├── environments/           # Root modules per environment
│   │   ├── _imported-demo/      # Adopts the hand-deployed demo via import blocks
│   │   ├── dev/ qa/ prod/ # Fixed shared environments
│   │   └── ephemeral/          # Per-developer disposable env (minelogx-dev-<user>)
│   └── imports/                # import {} blocks generated during demo import
├── cloudformation/             # Equivalent, deployable CFN definition (new envs)
│   ├── network/ s3/ iam/ lambda/ apigw/ eventbridge/
│   ├── step-functions/ opensearch-serverless/ bedrock-guardrails/
│   └── params/                 # <env>.params.json per environment
└── discovery/                  # gitignored — output of scripts/discover-aws.sh
```

## Ownership rule (important)

A live AWS resource can only be managed by **one** engine at a time. To avoid
drift/deletion conflicts:

- **Terraform owns the imported demo** — it is the source of truth for what is
  already deployed.
- **CloudFormation** holds an equivalent, deployable definition used to stand up
  **new** environments (ephemeral / dev / qa). It does not co-manage the
  demo resources.
- **Fabric** selects the engine per environment (`--engine=terraform|cloudformation`).

## Bootstrapping remote state (one-time per AWS account)

Creates the S3 state bucket (`minelogx-poc-terraform-state`) + DynamoDB lock table.
Shared by dev/qa/prod (isolated by per-env state keys). Idempotent:

```bash
uv run fab env.bootstrap
```

Run it once per account — and again when PROD moves to its own account. Fabric
then wires the backend automatically on `env.up`/`env.plan` via `-backend-config`.
