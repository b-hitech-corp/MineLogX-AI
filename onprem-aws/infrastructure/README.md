# infrastructure/

Infrastructure-as-Code for MineLogX-AI. Following the **dual-tool strategy**
(see `CLAUDE.md` → *IaC Strategy*), the platform is defined in **both**
Terraform and CloudFormation, and orchestrated per-environment by **Fabric**
(`fabfile.py`, task namespace `env.*`).

```
infrastructure/
├── terraform/                  # Terraform definition (state owner of the imported POC)
│   ├── versions.tf             # Terraform + AWS provider version constraints
│   ├── backend.tf              # Remote state (S3 + DynamoDB lock) — bootstrap first
│   ├── modules/                # Reusable modules (vpc, s3, lambda, api_gateway, ...)
│   ├── environments/           # Root modules per environment
│   │   ├── _imported-poc/      # Adopts the hand-deployed POC via import blocks
│   │   ├── dev/ qa/ prod/ # Fixed shared environments
│   │   └── ephemeral/          # Per-developer disposable env (minelogx-dev-<user>)
│   └── imports/                # import {} blocks generated during POC import
├── cloudformation/             # Equivalent, deployable CFN definition (new envs)
│   ├── network/ s3/ iam/ lambda/ apigw/ eventbridge/
│   ├── step-functions/ opensearch-serverless/ bedrock-guardrails/
│   └── params/                 # <env>.params.json per environment
└── discovery/                  # gitignored — output of scripts/discover-aws.sh
```

## Ownership rule (important)

A live AWS resource can only be managed by **one** engine at a time. To avoid
drift/deletion conflicts:

- **Terraform owns the imported POC** — it is the source of truth for what is
  already deployed.
- **CloudFormation** holds an equivalent, deployable definition used to stand up
  **new** environments (ephemeral / dev / qa). It does not co-manage the
  POC resources.
- **Fabric** selects the engine per environment (`--engine=terraform|cloudformation`).

## Bootstrapping remote state (one-time, before import)

```bash
aws s3api create-bucket --bucket minelogx-terraform-state --region us-east-1
aws s3api put-bucket-versioning --bucket minelogx-terraform-state \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name minelogx-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region us-east-1
```

Then uncomment the backend block in `terraform/backend.tf` and run `terraform init`.
