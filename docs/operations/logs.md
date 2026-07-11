# Activity Logs

Fabric writes structured, human-readable logs to `.fab-logs/` in the repo root (git-ignored).

---

## Log Files

| File pattern | Written by | Contains |
|---|---|---|
| `invoke-csv-<env>-<ts>.log` | `lambda.invoke csv` / `lambda.invoke-all csv` | Per-file Step Functions execution ARN, status, elapsed time |
| `invoke-pdf-<env>-<ts>.log` | `lambda.invoke pdf` / `lambda.invoke-all pdf` | Per-file Lambda status code, file key |
| `pdf-async-status-<env>-<ts>.log` | `lambda.pdf-async-status` | CloudWatch Logs Insights summary table |
| `opensearch-status-<env>-<ts>.log` | `opensearch.status` | Collection status, doc counts per index |
| `up-<env>-<ts>.log` | `env.up` (failure path only) | Full CFN deploy output captured on failure |
| `frontend-deploy-<env>-<ts>.log` | `frontend.deploy` | pnpm build output + Amplify deploy status |
| `env-health-<env>-<ts>.log` | `env.health` | Lambda states, AOSS doc counts, Step Functions history, Bedrock access |

---

## CloudWatch Logs

All Lambda functions write structured JSON logs to CloudWatch Log Groups:

| Log Group | Lambda |
|---|---|
| `/aws/lambda/minelogx-<env>-api` | API Lambda |
| `/aws/lambda/minelogx-<env>-csv` | CSV Pipeline Lambda |
| `/aws/lambda/minelogx-<env>-pdf` | PDF Pipeline Lambda |
| `/aws/states/minelogx-<env>-csv-pipeline` | Step Functions execution history |

### Tailing logs

```bash
# Follow API Lambda logs
uv run fab lambda.logs api dev --follow

# CloudWatch CLI equivalent
aws logs tail /aws/lambda/minelogx-dev-api --follow --region us-east-1
```

### PDF async status (CloudWatch Logs Insights)

```bash
# One-shot summary table of recent PDF invocations
uv run fab lambda.pdf-async-status

# Follow new completions in real time (15s polling)
uv run fab lambda.pdf-async-status --follow
```

---

## Structured Logging Pattern (Lambda)

All Lambda handlers must log structured JSON:

```python
import json, logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    logger.info(json.dumps({
        "event": "lambda_invoked",
        "function": context.function_name,
        "request_id": context.aws_request_id
    }))
```
