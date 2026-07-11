# MineLogX AI

**Operational intelligence platform for mining operations** — combining IoT telemetry analytics,
machine learning anomaly detection, and regulatory compliance Q&A powered by Amazon Bedrock.

---

## Key Features

| Capability | Description |
|---|---|
| Protocol adapters | IP21, OSI PI, OPC UA, Modbus, MQTT |
| AI analytics agents | KPI calculation, anomaly detection, telemetry insights |
| Compliance Q&A | Natural language regulatory Q&A with traceable citations |
| Semantic search | Hybrid vector + lexical search across structured and unstructured mining data |
| Guardrails | Prompt-injection defense, PII filtering, topic denial at every AI touchpoint |
| Reporting | Dashboards and ESG-ready reports |
| IaC | Cloud-agnostic deployment via AWS, Azure, IBM Cloud, on-prem, and Snowflake-backed variants |

---

## Project Structure

```
MineLogX-AI/
├── README.md  CONTRIBUTING.md  CLAUDE.md  AGENTS.md
├── fabfile.py                        # Fabric orchestrator — all automation lives here
├── pyproject.toml  uv.lock           # uv, Python >= 3.11
├── docs/                             # This documentation site
├── shared/                           # Cloud-agnostic core (frontend, modules, connectors)
│   └── frontend/                     # React 19 / Vite / Tailwind app (AWS Amplify)
└── onprem-aws/                       # AWS reference implementation
    ├── infrastructure/
    │   ├── cloudformation/           # New environments (dev, qa, ephemeral)
    │   └── terraform/                # State owner of the imported demo
    └── backend/                      # Lambda + Bedrock agent code
```

!!! note "Deployment targets"
    Only `onprem-aws` (the AWS reference implementation) and `shared/` exist today.
    `onprem-azure`, `onprem-ibm`, and `onprem-only` are roadmap items added when a client needs them.

---

## AI Agents

| Agent | Purpose |
|---|---|
| **Data Analysis Agent** | Calculates fleet KPIs (fuel efficiency, utilization, MTBF, idle rate, OTD, CO₂/km), detects anomalies, and generates business-readable insights from validated telemetry data. |
| **RAG Compliance Agent** | Answers natural-language regulatory questions grounded in jurisdiction-specific legal documents, returning traceable citations. Advisory only — not legal counsel. |

Both agents enforce the same baseline rules: no fabricated KPIs or citations, no cross-tenant data leakage,
no raw/unvalidated data reaching an embedding model, and guardrails applied at every touchpoint.

---

## Roadmap

- ✅ IoT protocol support (IP21, OSI PI, OPC UA, Modbus, MQTT)
- ✅ AWS reference implementation (Bedrock + OpenSearch Serverless)
- ✅ Multi-model RAG agent (Claude Sonnet / Nova Pro / DeepSeek V3.2)
- ⏳ Azure AI and IBM Watsonx agent implementations
- ⏳ Grafana-compatible exporter
- ⏳ CI/CD with GitHub Actions across all deployment targets
- ⏳ Advanced NLP summaries across providers
- ⏳ Multi-cloud deployment templates
