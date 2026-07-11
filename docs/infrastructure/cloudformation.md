# CloudFormation Stacks

The CloudFormation definition in `onprem-aws/infrastructure/cloudformation/` is used to deploy
**new environments** (dev, qa, ephemeral). It does not manage the imported demo directly.

---

## Stack Layers

`parent.yaml` composes all nested stacks. Each layer is a separate stack with the naming
convention `minelogx-<env>-<layer>`:

| Stack | Template | Provisions |
|---|---|---|
| `minelogx-<env>-network` | `network/network.yaml` | VPC, subnets, internet/NAT gateways (BYOVPC-aware) |
| `minelogx-<env>-security-groups` | `security-groups/security-groups.yaml` | SGs for Lambda, EC2 |
| `minelogx-<env>-s3` | `s3/s3.yaml` | Telemetry data bucket, legislation docs bucket |
| `minelogx-<env>-iam` | `iam/iam.yaml` | IAM roles for api/csv/pdf Lambdas |
| `minelogx-<env>-cloudwatch` | `cloudwatch/cloudwatch.yaml` | Log groups |
| `minelogx-<env>-apigw` | `apigw/apigw.yaml` | HTTP API v2, CORS, Lambda proxy integration |
| `minelogx-<env>-amplify` | `amplify/amplify.yaml` | Amplify App + Branch for the React frontend |
| `minelogx-<env>-ec2` | `ec2/ec2-llm.yaml` | EC2 Ollama instances (demo fallback, `Condition: EnableLlmFallback`) |
| `minelogx-<env>-opensearch` | `opensearch-serverless/opensearch-serverless.yaml` | AOSS collection, access policies |
| `minelogx-<env>-guardrails` | `bedrock-guardrails/bedrock-guardrails.yaml` | Bedrock Guardrail |
| `minelogx-<env>-lambda` | `lambda/lambda.yaml` | api/csv/pdf Lambda functions + layers |
| `minelogx-<env>-step-functions` | `step-functions/step-functions.yaml` | CSV pipeline state machine |
| `minelogx-<env>-eventbridge` | `eventbridge/eventbridge.yaml` | Daily CSV scheduler + PDF S3 rule |

---

## Parent Stack Parameters

| Parameter | Default | Notes |
|---|---|---|
| `NamePrefix` | — | Required. e.g. `minelogx` |
| `Environment` | — | Required. `dev`, `qa`, `prod`, or `dev-<user>` |
| `ProjectApnId` | `pc:13uw3s8iyvze74tlcq3o0w8r6` | AWS APN ID for tagging |
| `EnableLlmFallback` | `false` | Set `true` to provision EC2 Ollama instances |
| `ExistingVpcId` | `` (empty) | BYOVPC — leave empty to create a new VPC |
| `BuildCsvLayer` | `false` | Publish CSV deps Lambda layer (requires prior `fab lambda.build-layer csv`) |
| `BuildPdfLayer` | `false` | Publish PDF deps Lambda layer |
| `ViteUseMock` | `false` | Passed to Amplify app as env var |

---

## Per-environment Parameters

Each environment has a parameter file in `params/<env>.json`:

```json
[
  { "ParameterKey": "NamePrefix",       "ParameterValue": "minelogx" },
  { "ParameterKey": "Environment",      "ParameterValue": "dev" },
  { "ParameterKey": "ExistingVpcId",    "ParameterValue": "vpc-0a7b98533f5eaa246" },
  { "ParameterKey": "ExistingPublicSubnet1Id", "ParameterValue": "subnet-..." }
]
```

For sensitive overrides (not committed), create `params/<env>.local.json` — gitignored.

---

## Deploying and Validating

```bash
# Deploy (CloudFormation engine — default)
uv run fab env.up dev

# Preview change set only (no apply)
uv run fab env.plan dev

# Validate a single template
aws cloudformation validate-template \
  --template-body file://onprem-aws/infrastructure/cloudformation/lambda/lambda.yaml

# Check stack outputs
uv run fab env.endpoints dev
```

---

## Auto-recover from ROLLBACK_COMPLETE

`fab env.up` automatically detects stacks in `ROLLBACK_COMPLETE`, deletes them, and recreates.
No manual intervention needed.
