# CloudFormation definition

An **equivalent, deployable** definition of the MineLogX-AI architecture, used
to stand up **new** environments (ephemeral / dev / qa). It does **not**
co-manage the hand-deployed POC resources — those are owned by Terraform
(see `../README.md` → ownership rule).

Every stack:
- is named `minelogx-<env>-<layer>` (e.g. `minelogx-dev-cesar-network`);
- tags all resources with `aws-apn-id`, `Environment`, `ManagedBy=cloudformation`;
- takes its parameters from `params/<env>.params.json`.

## Layers (one stack per folder)

| Folder                   | Stack contents                                             |
|--------------------------|------------------------------------------------------------|
| `network/`               | VPC, subnets, route tables, IGW/NAT, security groups       |
| `s3/`                    | Data lake bucket + lifecycle + prefix layout               |
| `iam/`                   | Roles/policies (least privilege)                           |
| `lambda/`                | The 8 `minelogx-*` API Lambdas + pipeline Lambdas          |
| `apigw/`                 | REST API fronting the Lambdas                              |
| `eventbridge/`           | Rules + Scheduler for CSV/PDF pipelines                    |
| `step-functions/`        | CSV / PDF vectorization state machines                     |
| `opensearch-serverless/` | Serverless collection + indices                            |
| `bedrock-guardrails/`    | `iot-mining-poc-guardrail-v1`                              |

## Deploy (via Fabric)

```bash
fab env.up   --env=dev-cesar --engine=cloudformation
fab env.down --env=dev-cesar --engine=cloudformation
```

Manual equivalent:

```bash
aws cloudformation validate-template --template-body file://network/network.yaml
aws cloudformation deploy \
  --template-file network/network.yaml \
  --stack-name minelogx-dev-cesar-network \
  --parameter-overrides file://params/dev-cesar.params.json \
  --capabilities CAPABILITY_NAMED_IAM --region us-east-1
```
