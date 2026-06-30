```markdown
<p align="center">
  <img src="docs/assets/minelogx-logo.png" width="224" alt="MineLogX AI Logo" />
</p>

<h1 align="center">🧠 MineLogX AI</h1>

<p align="center">
  <em>A cloud-agnostic, open-source AI platform transforming mining operations through unified IoT data, analytics, and generative AI intelligence.</em>
</p>

<p align="center">
  <a href="https://github.com/b-hitech-corp/MineLogX-AI">
    <img src="https://img.shields.io/github/stars/yourusername/MineLogX-AI?style=social" alt="GitHub stars">
  </a>
  <a href="https://github.com/b-hitech-corp/MineLogX-AI/issues">
    <img src="https://img.shields.io/github/issues/b-hitech-corp/MineLogX-AI" alt="GitHub issues">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License">
  </a>
</p>

---

## 🌍 Overview

**MineLogX AI** is a cloud-agnostic, open-source platform built for mining companies seeking real-time insight, safety, and sustainability. It standardizes fragmented IoT data from legacy protocols like IP21, OSI PI, OPC UA, Modbus, and MQTT into a unified schema, ready for analytics and AI — regardless of which cloud, or no cloud, it runs on.

The platform ships with AI-powered agents for telemetry analysis and regulatory compliance Q&A. These agents are defined against a **provider-agnostic interface**, with reference implementations available for AWS Bedrock today, and additional providers (Azure AI, IBM Watsonx, on-prem LLMs) on the roadmap. No part of the core platform — data model, pipelines, or agent contracts — is locked to a single vendor.

> 📌 **Cloud-agnostic by design.** Every deployment target (AWS, Azure, IBM, on-prem, or hybrid with Snowflake) implements the same data contracts and agent interfaces, so teams can choose — or switch — infrastructure without rewriting the platform.

---

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

- Python 3.9+
- pip
- Terraform (primary IaC tool across all targets)
- (Optional) Docker
- Cloud CLI for your chosen target (AWS CLI, Azure CLI, IBM Cloud CLI) — not required for on-prem-only deployments

### 🧑‍💻 Install & Run

```bash
git clone https://github.com/yourusername/MineLogX-AI.git
cd MineLogX-AI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### 🐳 Docker Option

```bash
docker build -t minelogx-ai .
docker run -v $(pwd)/data:/app/data minelogx-ai --log data/sample.log
```

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
MinelogX-AI-framework/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── docs/
│   ├── architecture.md
│   ├── usage-examples.md
│   ├── development-guide.md
│   ├── cloud-setup-guides/
│   │   ├── aws.md
│   │   ├── azure.md
│   │   ├── ibm-cloud.md
│   │   ├── snowflake.md
│   └── on-prem.md
├── onprem-only/
│   ├── infrastructure/
│   │   ├── terraform/
│   │   ├── ansible/
│   │   └── k8s/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── onprem-aws/
│   ├── infrastructure/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── onprem-aws-snowflake/
│   ├── infrastructure/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── onprem-azure/
│   ├── infrastructure/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── onprem-azure-snowflake/
│   ├── infrastructure/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── onprem-ibm/
│   ├── infrastructure/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── onprem-ibm-snowflake/
│   ├── infrastructure/
│   ├── pipelines/
│   ├── connectors/
│   ├── modules/
│   ├── tests/
│   ├── scripts/
│   └── README.md
├── shared/
│   ├── modules/
│   ├── connectors/
│   ├── templates/
│   └── README.md
├── .github/
│   ├── workflows/   # CI/CD actions
│   ├── ISSUE_TEMPLATE.md
│   └── PULL_REQUEST_TEMPLATE.md
└── LICENSE
```

`shared/` holds the cloud-agnostic core: protocol adapters, data schemas, agent contracts, and templates that every `onprem-*` deployment target consumes. Provider-specific folders (`onprem-aws`, `onprem-azure`, `onprem-ibm`, and their Snowflake-paired variants) implement those contracts using each provider's native services.

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
