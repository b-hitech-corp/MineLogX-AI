# onprem-aws

AWS deployment target for the MineLogX-AI framework — the reference
implementation (Bedrock + OpenSearch/S3 Vectors + Lambda + API Gateway).

## Layout

```
onprem-aws/
├── infrastructure/     # IaC — dual Terraform + CloudFormation (see infrastructure/README.md)
├── scripts/            # discover-aws.{sh,ps1} — read-only account inventory
├── pipelines/          # (planned) CSV telemetry + PDF legal vectorization pipelines
├── connectors/         # (planned) protocol adapters (IP21, OSI PI, OPC UA, Modbus, MQTT)
├── modules/            # (planned) AWS-specific reusable app modules
└── tests/              # (planned) target tests
```

`pipelines/`, `connectors/`, `modules/`, `tests/` follow the client's framework
template; they are created as real code lands (YAGNI — no empty scaffolding).

## Workflow

- Orchestrated from the repo-root `fabfile.py` (`fab env.*`, `--engine=terraform|cloudformation`).
- Cross-target reusable code lives in the repo-root `shared/`.
- AWS access + discovery + import: see the root [`CONTRIBUTING.md`](../CONTRIBUTING.md).
