# Terraform

Terraform is the **state owner of the imported demo** environment. It is not used for
new environment creation — that is handled by CloudFormation (see [CloudFormation Stacks](cloudformation.md)).

---

## Ownership Rule

| Engine | Owns |
|---|---|
| **Terraform** | `environments/_imported-demo` — the hand-deployed demo resources |
| **CloudFormation** | All new environments (`dev`, `qa`, `prod`, `dev-<user>`) |

Changing a resource in the demo without going through Terraform risks state drift.
Never apply raw `aws` CLI commands against demo resources without reflecting them in TF state.

---

## Structure

```
onprem-aws/infrastructure/terraform/
├── modules/           # vpc, security_groups, s3, iam, lambda, api_gateway, ...
├── environments/
│   ├── _imported-demo/   # State owner of the existing demo — do not recreate
│   ├── dev/              # Fixed dev environment
│   ├── qa/               # Fixed QA environment
│   ├── prod/             # Fixed prod — guarded, never destroy manually
│   └── ephemeral/        # Per-developer isolated workspaces
└── imports/              # import {} blocks for demo resources
```

---

## Remote State

```hcl
terraform {
  backend "s3" {
    bucket         = "minelogx-poc-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "minelogx-terraform-locks"
    encrypt        = true
  }
}
```

Bootstrap (one-time per account):
```bash
uv run fab env.bootstrap
```

---

## Applying Changes

Always plan before apply — never apply without reviewing the plan output.

```bash
# Initialize (first time or after provider changes)
terraform init

# Format and validate
terraform fmt -recursive
terraform validate

# Plan
terraform plan -out=tfplan

# Apply (only after reviewing the plan)
terraform apply tfplan
```

---

## Discovery → Import Workflow

Used when capturing existing AWS resources into Terraform state:

```bash
# 1. Snapshot the deployed demo (read-only)
bash onprem-aws/scripts/discover-aws.sh
# Output lands in infrastructure/discovery/ (gitignored — contains account IDs/ARNs)

# 2. Write import {} blocks in infrastructure/terraform/imports/

# 3. Generate config
terraform plan -generate-config-out=generated.tf

# 4. Refactor into modules/, apply, confirm 0 changes
terraform apply    # imports only
terraform plan     # verify: 0 changes
```

---

## Fabric + Terraform

Fabric can drive Terraform environments via `--engine terraform`:

```bash
uv run fab env.up dev-cesar --engine terraform
uv run fab env.plan dev --engine terraform
uv run fab env.down dev-cesar --engine terraform
```
