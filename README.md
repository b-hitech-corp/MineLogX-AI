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
uv run fab --list                    # list available tasks
uv run fab env.plan dev-cesar cf     # preview a CloudFormation environment
uv run fab env.up   dev-cesar        # deploy with Terraform (default engine)
```

One-time state backend, before the first Terraform deploy:

```bash
bash onprem-aws/scripts/bootstrap-backend.sh
```

For AWS SSO access, the demo → IaC import flow, and full conventions, see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

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

## 🚀 Environments (Fabric)

Both IaC engines deploy the same environment through Fabric. `env` and `engine`
are **positional** (engine defaults to `terraform`; aliases `tf` / `cf`):

```bash
fab env.up   dev-cesar        # Terraform (default engine)
fab env.plan dev-cesar cf     # CloudFormation (one nested stack: minelogx-dev-cesar)
fab env.down dev-cesar
fab env.list
```

- **Fixed** envs: `dev` / `qa` / `prod`. **Ephemeral** per-dev: `dev-<name>`
  (isolated by Terraform workspace / CFN stack `minelogx-dev-<name>`).
- Drop the `uv run` prefix by activating the venv
  (`source .venv/Scripts/activate`) or `alias mlx='uv run fab'`.
- One-time state backend bootstrap: `bash onprem-aws/scripts/bootstrap-backend.sh`.

Full dev setup and conventions: [`CONTRIBUTING.md`](CONTRIBUTING.md).

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

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

### 🚧 Let's build the future of intelligent mining together — with open data, AI, and freedom of cloud choice.
```
