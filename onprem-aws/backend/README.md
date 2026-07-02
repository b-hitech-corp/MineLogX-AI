# onprem-aws/backend

Application code for the AWS target: Lambda functions and Bedrock agents.

## Where code goes

```
backend/
├── lambdas/
│   ├── ml/                     # → deployed as minelogx-<env>-ml
│   │   ├── lambda_function.py  # entrypoint: def lambda_handler(event, context)
│   │   └── requirements.txt    # dependencies for THIS function only
│   ├── rag/                    # → minelogx-<env>-rag
│   └── <other-fn>/             # one folder per Lambda (chunker, pdf-processor, ...)
└── agents/                     # Bedrock agents (NOT Lambdas): data-analysis, rag-agent
```

Conventions (match the imported demo):
- Runtime **Python 3.11**, handler **`lambda_function.lambda_handler`**.
- One folder per function; keep the handler thin.
- **Cloud-agnostic logic** (KPI math, parsing, chunking) goes in the repo-root
  `shared/` and is imported here — so Azure/IBM targets can reuse it.

## How it reaches AWS

The Terraform `lambda` module (and the CloudFormation `lambda` stack) package each
folder into a zip and wire it to the function. The Lambda import blocks in
`infrastructure/terraform/environments/_imported-demo/demo-imports.tf` are currently
**deferred** precisely because the code artifact wasn't in the repo yet — once the
code lands here, we re-enable them (zip + `source_code_hash`) and deploy via
`fab env.up`.
