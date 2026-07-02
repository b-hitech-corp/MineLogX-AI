# Terraform modules

Reusable building blocks consumed by the root modules under `../environments/`.
Each module lives in its own folder with `main.tf`, `variables.tf`, `outputs.tf`.

Planned modules (created as the demo import and target architecture progress):

| Module            | Purpose                                                      |
|-------------------|--------------------------------------------------------------|
| `vpc`             | VPC, subnets (public/private), route tables, IGW, NAT        |
| `security_groups` | Security groups per layer                                    |
| `s3`              | Buckets + lifecycle + strict prefix layout (`iot-mining-poc`)|
| `iam`             | Roles/policies (least privilege) for Lambda / pipelines      |
| `lambda`          | The 8 `minelogx-*` API Lambdas + pipeline Lambdas            |
| `api_gateway`     | REST API fronting the Lambdas                                |
| `ec2`             | Ollama demo instances (to be retired for Bedrock)             |
| `eventbridge`     | Rules + Scheduler for the CSV/PDF pipelines                  |
| `step_functions`  | CSV / PDF vectorization state machines                       |
| `opensearch`      | Serverless collection + `csv_telemetry_vecs`/`pdf_legal_vecs`|
| `bedrock`         | Guardrail + model access wiring                              |

Modules for the target architecture (Bedrock pipelines, OpenSearch, Step
Functions, Amplify, Textract) are implemented in a later phase — see the plan.
