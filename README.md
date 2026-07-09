## ⚙️ Key Features

- 📡 Protocol adapters for IP21, OSI PI, OPC UA, Modbus, MQTT
- 🧠 AI-powered analytics agents — KPI calculation, anomaly detection, telemetry insights
- 💬 Natural language compliance Q&A grounded in regulatory documents, with traceable citations
- 🧾 Semantic + hybrid search (vector + lexical) across structured/unstructured mining data
- 🛡️ Guardrails enforced at every AI touchpoint — prompt-injection defense, PII filtering, topic denial
- 📊 Dashboards and ESG-ready reporting tools
- 🔁 Cloud-agnostic, infrastructure-as-code deployment across AWS, Azure, IBM Cloud, on-prem, and Snowflake-backed variants

---

## 🚀 Quick Start

### 🛠 Requirements

- [uv](https://docs.astral.sh/uv/) — manages the Python env (requires Python **3.11+**)
- AWS CLI v2 — for the AWS target (SSO login)
- Terraform >= 1.5 — only if you deploy through the Terraform engine
- (Optional) Node 20 + pnpm — only if you work on the frontend

### 🧑‍💻 Install & Run

Clone the repo and run the **one-command dev setup** — it creates the virtualenv,
installs dependencies (via uv), and wires the pre-commit git hooks:

```bash
git clone git@github.com:b-hitech-corp/MineLogX-AI.git
cd MineLogX-AI
bash scripts/dev-setup.sh          # Windows PowerShell: ./scripts/dev-setup.ps1
```

Then drive environments with Fabric (no venv activation needed — `uv run` handles it):

```bash
uv run fab --list                         # list all available tasks
uv run fab env.plan dev --engine cf       # preview a CloudFormation change set
uv run fab env.up   dev --seed            # deploy infra + frontend full-stack (+ seed S3)
uv run fab env.up   dev --skip-frontend   # solo infra, sin rebuild del frontend
uv run fab frontend.deploy dev            # rebuild y redeploy solo el frontend
```

`env.up` realiza el ciclo completo:
1. Build Lambda layers (csv, pdf)
2. Deploy CloudFormation (HTTP API Gateway v2, Lambda, Amplify, OpenSearch…)
3. Obtiene `ApiUrl` del stack output → inyecta `VITE_API_BASE_URL` en el build de Vite
4. `pnpm type-check` + `pnpm build` + upload a Amplify
5. Imprime la URL del frontend al finalizar

One-time state backend bootstrap (run once per account):

```bash
uv run fab env.bootstrap
```

For the demo → IaC import flow and full conventions, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

### 🔐 AWS Authentication

All Fabric tasks assume an AWS CLI profile named **`minelogx-admin`** that has
access to the POC account (`586928288932`) via SSO assume-role.

**One-time SSO profile setup:**

```bash
aws configure sso --profile minelogx-admin
# SSO start URL : https://d-9067e84741.awsapps.com/start
# SSO region    : us-east-1
# Account       : 586928288932
# Role          : AvahiAdminAccess
```

**Per-session login** (token expires after ~8 h):

```bash
aws sso login --profile minelogx-admin
```

**Override the default profile** (e.g. CI or a different account):

```bash
export MINELOGX_AWS_PROFILE=my-other-profile   # overrides minelogx-admin
export AWS_REGION=us-west-2                    # overrides us-east-1 (default)
```

All other Fabric environment variables and their defaults:

| Variable | Default | Purpose |
|---|---|---|
| `MINELOGX_AWS_PROFILE` | `minelogx-admin` | AWS CLI profile used for all operations |
| `AWS_REGION` | `us-east-1` | Target region |
| `CFN_TEMPLATE_BUCKET` | `minelogx-poc-cfn-templates` | S3 bucket for nested CFN template uploads |
| `AWS_SSO_PROFILE` | `125396563242_B_Hitech-586928288932` | SSO hub profile for auto-token refresh |
| `MINELOGX_TARGET` | `onprem-aws` | Deployment target folder |
| `EC2_KEY_PATH` | `~/.ssh/minelogx-demo-poc-keypair.pem` | SSH key for Ollama EC2 instances |
| `TERRAFORM_BIN` | auto-detected | Override Terraform binary path |

### ☁️ Choosing a Deployment Target

MineLogX AI separates the **platform logic** (pipelines, schemas, agent contracts) from the **deployment target** (cloud or on-prem). Pick the folder matching your environment and follow its own `README.md` for provider-specific setup — each one walks through standing up the equivalent stack (storage, vector search, IaC, and an AI agent provider) for that environment:

```bash
cd onprem-aws && cat README.md       # AWS reference implementation
cd onprem-azure && cat README.md     # Azure implementation
cd onprem-ibm && cat README.md       # IBM Cloud implementation
cd onprem-only && cat README.md      # Fully on-prem, no cloud dependency
```

See [`docs/cloud-setup-guides/`](docs/cloud-setup-guides/) for a deeper walkthrough of each provider.

---

## 🧱 Project Structure

```
MineLogX-AI/
├── README.md  LICENSE  CONTRIBUTING.md
├── pyproject.toml  uv.lock  .python-version     # uv, Python >= 3.11
├── .pre-commit-config.yaml  .yamllint  .gitattributes
├── fabfile.py                                   # Fabric orchestrator (env.* + ollama.*)
├── .github/
│   ├── workflows/lint.yml                       # CI: ruff, bandit, pip-audit, yamllint, gitleaks, web
│   ├── ISSUE_TEMPLATE.md
│   └── PULL_REQUEST_TEMPLATE.md
├── docs/                                        # architecture, api, cloud-setup guides
├── shared/                                      # cloud-agnostic core
│   ├── modules/  connectors/  templates/
│   ├── frontend/            # React app / AWS Amplify (cloud-agnostic UI)
│   └── README.md
├── onprem-aws/                                  # ✅ AWS target — reference implementation
│   ├── infrastructure/
│   │   ├── terraform/       # state owner of the imported demo (+ environments/{dev,qa,prod,ephemeral}, modules/, imports/)
│   │   └── cloudformation/  # equivalent CFN definition for new environments
│   ├── backend/             # Lambda + Bedrock agent code
│   ├── scripts/             # discover-aws.{sh,ps1} — read-only account inventory
│   ├── (planned) pipelines/  connectors/  modules/  tests/
│   └── README.md
└── (planned) onprem-azure/  onprem-ibm/  onprem-*-snowflake/  onprem-only/
```

Only **`onprem-aws`** (the reference implementation) and **`shared`** exist today.
The other deployment targets (`onprem-azure`, `onprem-ibm`, the Snowflake-paired
variants, `onprem-only`) are on the roadmap and added when a client needs them —
we don't scaffold empty target trees.

`shared/` holds the cloud-agnostic core: protocol adapters, data schemas, agent contracts, and templates that every `onprem-*` deployment target consumes. Provider-specific folders implement those contracts using each provider's native services. Repo-wide tooling (uv, pre-commit, CI, Fabric) lives at the root.

---

## 🚀 Fabric Tasks Reference

All commands use `uv run fab <namespace>.<task> [args]`. Fabric reserves `-e` for
`--echo`, so always use the long `--engine` flag.

### env.* — Environment lifecycle

```bash
uv run fab env.up   dev --seed        # deploy CloudFormation + seed S3 from demo buckets
uv run fab env.up   dev               # deploy without seeding
uv run fab env.plan dev               # preview changes (CFN change set, no apply)
uv run fab env.down dev               # destroy the environment
uv run fab env.list                   # list active CFN stacks and TF workspaces
uv run fab env.endpoints dev          # print live URLs (API Gateway, Amplify, OpenSearch)
uv run fab env.bootstrap              # create the S3 bucket for CFN template uploads (once per account)
```

Engine defaults to `cloudformation`. Override with `--engine terraform` (alias `tf` / `cf`).

**Fixed** envs: `dev` / `qa` / `prod`.
**Ephemeral** per-dev: `dev-<name>` (e.g. `dev-cesar`) — isolated by CFN stack prefix / TF workspace.

### lambda.* — Pipeline invocation and layer builds

```bash
uv run fab lambda.invoke csv dev                         # trigger CSV pipeline (Step Functions)
uv run fab lambda.invoke csv dev --wait                  # trigger + block until complete
uv run fab lambda.invoke csv dev --file-path C1/foo.csv  # use a specific S3 key
uv run fab lambda.invoke pdf dev                         # invoke PDF Lambda with synthetic S3 event
uv run fab lambda.invoke pdf dev --async                 # fire-and-forget (InvocationType=Event)
uv run fab lambda.invoke-all csv dev --parallel          # process every S3 CSV in parallel
uv run fab lambda.invoke-all pdf dev --async             # queue every S3 PDF asynchronously
uv run fab lambda.pdf-async-status dev                   # CloudWatch Logs Insights status table
uv run fab lambda.build-layer csv                        # build the CSV deps layer (no Docker)
uv run fab lambda.build-layer pdf                        # build the PDF deps layer (no Docker)
uv run fab lambda.pull                                   # download deployed demo Lambda code
```

### opensearch.* — Collection and index status

```bash
uv run fab opensearch.status dev      # collection status + document count per index
```

Prints collection health and doc counts for `csv_telemetry_vecs` and `pdf_legal_vecs`.
Saves a formatted log to `.fab-logs/opensearch-status-dev-<ts>.log`.

### frontend.* — Amplify deployment

```bash
uv run fab frontend.deploy dev        # build React/Vite app and push to Amplify
uv run fab frontend.deploy dev --skip-build  # re-deploy using an existing dist/
```

### ollama.* — Demo EC2 remote ops (demo only)

```bash
uv run fab ollama.health-check        # check all Ollama instances
uv run fab ollama.restart-ollama      # restart Ollama container on all instances
uv run fab ollama.pull-model --host qwen3 --model qwen3:8b
uv run fab ollama.logs --host gemma3
```

### Activity logs

Fabric writes structured, human-readable logs to `.fab-logs/` (git-ignored):

| File pattern | Written by |
|---|---|
| `invoke-csv-<env>-<ts>.log` | `lambda.invoke csv` / `lambda.invoke-all csv` |
| `invoke-pdf-<env>-<ts>.log` | `lambda.invoke pdf` / `lambda.invoke-all pdf` |
| `pdf-async-status-<env>-<ts>.log` | `lambda.pdf-async-status` |
| `opensearch-status-<env>-<ts>.log` | `opensearch.status` |
| `up-<env>-<ts>.log` | `env.up` (on failure only) |

---

## 🤖 AI Agents

MineLogX ships two reference agent types, defined behind a provider-agnostic interface so the same capabilities can run on any supported cloud:

| Agent | Purpose |
|---|---|
| **Data Analysis Agent** | Calculates fleet KPIs (fuel efficiency, utilization, MTBF, idle rate, OTD, CO₂/km, and more), detects anomalies, and generates business-readable insights from validated telemetry data — never from raw, unvalidated input. |
| **RAG Compliance Agent** | Answers natural-language regulatory questions grounded in jurisdiction-specific legal documents, returning traceable citations. Advisory only — not legal counsel. |

Every agent, regardless of provider, enforces the same baseline rules: no fabricated KPIs or citations, no cross-tenant data leakage, no raw/unvalidated data reaching an embedding model, and guardrails (prompt-injection defense, PII filtering, topic denial) applied at every touchpoint. The AWS reference implementation (Amazon Bedrock + OpenSearch Serverless) is documented in `onprem-aws/README.md`; equivalent Azure AI and IBM Watsonx implementations are tracked on the roadmap below.

---

## 🔭 Roadmap

* ✅ Support for major IoT protocols
* ✅ AWS reference implementation (Bedrock + OpenSearch Serverless)
* ⏳ Azure AI and IBM Watsonx agent implementations
* ⏳ Grafana-compatible exporter
* ⏳ CI/CD with GitHub Actions across all deployment targets
* ⏳ Advanced NLP summaries across providers
* ⏳ Multi-cloud deployment templates and one-command provider switching

---

## 🤝 Contributing

We welcome contributions! Here's how to get started:

1. Fork the repository 🍴
2. Create a new branch: `git checkout -b feature/amazing-feature`
3. Commit your changes 📝
4. Push to your fork: `git push origin feature/amazing-feature`
5. Submit a Pull Request ✅

When contributing to a specific cloud implementation, please keep provider-specific code inside its `onprem-*` folder and put any reusable logic in `shared/` so other providers can benefit from it too.

## 📄 Licenses

This project is licensed under the [MIT License](https://mit-license.org/) and [Apache License](https://www.apache.org/licenses/LICENSE-2.0)

---

### 🚧 Let's build the future of intelligent mining together — with open data, AI, and freedom of cloud choice with MLX Ai.
```
