# Infrastructure Overview

MineLogX AI uses a **dual-tool IaC strategy**: Terraform and CloudFormation definitions are kept
at parity, with Fabric selecting the engine per environment.

---

## Structure

```
onprem-aws/
└── infrastructure/
    ├── cloudformation/     # Equivalent CFN definition — used for new environments
    │   ├── parent.yaml     # Composer — references all nested stacks
    │   ├── amplify/
    │   ├── apigw/
    │   ├── bedrock-guardrails/
    │   ├── cloudwatch/
    │   ├── ec2/
    │   ├── eventbridge/
    │   ├── iam/
    │   ├── lambda/
    │   ├── network/
    │   ├── opensearch-serverless/
    │   ├── s3/
    │   ├── security-groups/
    │   ├── step-functions/
    │   └── params/         # Per-environment parameter files
    └── terraform/          # State owner of the imported demo
        ├── modules/        # vpc, s3, iam, lambda, api_gateway, ...
        ├── environments/   # _imported-demo, dev, qa, prod, ephemeral
        └── imports/        # import {} blocks for the demo resources
```

---

## Ownership Rule

!!! warning "One engine per resource"
    A live AWS resource can be managed by only **one** engine at a time.

- **Terraform** owns the imported demo (`environments/_imported-demo`). It is the source of truth for what is already deployed.
- **CloudFormation** holds an equivalent, deployable definition used to stand up **new** environments (`dev`, `qa`, ephemeral). It does **not** co-manage the demo's live resources.
- When adding or changing infrastructure, update **both** definitions and keep them at parity.

---

## Environments

| Type | Naming | Isolation |
|---|---|---|
| Fixed shared | `dev`, `qa`, `prod` | Dedicated Terraform root + CFN param file |
| Ephemeral per-developer | `dev-<user>` (e.g. `dev-cesar`) | Terraform workspace or CFN stack prefix `minelogx-dev-cesar-*` |

All environments are driven through Fabric — never by hand in the console.

---

## Tagging

Every resource carries these tags:

| Tag | Value |
|---|---|
| `aws-apn-id` | `pc:13uw3s8iyvze74tlcq3o0w8r6` |
| `Environment` | `dev` / `qa` / `prod` / `dev-<user>` |
| `ManagedBy` | `terraform` or `cloudformation` |

---

## Deploy Commands

```bash
# CloudFormation (default engine)
uv run fab env.up dev                       # full deploy
uv run fab env.up dev --skip-frontend       # infra only
uv run fab env.plan dev                     # preview (CFN change set, no apply)
uv run fab env.down dev                     # destroy

# Terraform (explicit engine)
uv run fab env.up dev-cesar --engine terraform
uv run fab env.plan dev --engine terraform
```

For the full Fabric task reference, see [Fabric Task Reference](../operations/fabric-tasks.md).
