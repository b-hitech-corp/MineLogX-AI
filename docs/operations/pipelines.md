# Pipelines (CSV & PDF)

This page covers how to run, monitor, and troubleshoot the two ingestion pipelines.
For the architecture of each pipeline, see [Data Flow](../architecture/data-flow.md).

---

## CSV Telemetry Pipeline

### Running the pipeline

```bash
# Process all CSV files in S3 (parallel — recommended for initial ingestion)
uv run fab lambda.invoke-all csv dev --parallel

# Process a single file
uv run fab lambda.invoke csv dev --file-path C1/fuel_management_events.csv --wait

# Force re-process (skip deduplication)
uv run fab lambda.invoke csv dev --file-path C1/fleet.csv --force
```

### Monitoring

```bash
# Check Step Functions execution history
uv run fab step-functions.history dev

# Check OpenSearch doc count after ingestion
uv run fab opensearch.status dev
```

### Current status (dev environment)

15/15 CSV files SUCCEEDED across three client datasets:

| Dataset | Files |
|---|---|
| C1 | fuel_management_events, assets_equipment, mine_locations, production_kpi_daily, gps_movement_logs |
| C2 | ground_vibration_dataset, haul_cycle_tracking, maintenance_recommendations, tire_pressure_monitoring |
| C3 | fuel_management_events, gps_movement_logs, mine_locations, mining_truck_fleet_mock, operator_fatigue_events, safety_environmental_events |

---

## PDF Legal Document Pipeline

### Running the pipeline

```bash
# Process all PDFs in S3 (serial — safer for event-driven Lambda)
uv run fab lambda.invoke-all pdf dev

# Fire-and-forget (async — useful for large batches)
uv run fab lambda.invoke-all pdf dev --async

# Monitor async invocations via CloudWatch Logs Insights
uv run fab lambda.pdf-async-status

# Follow new completions in real time
uv run fab lambda.pdf-async-status --follow
```

### Monitoring

The `pdf-async-status` task queries CloudWatch Logs Insights and shows a summary table:

```
  PDF  |  Status   |  Sections  |  Pages  |  Duration
  ---  |  -------  |  --------  |  -------  |  --------
  mining_code_senegal.pdf  |  ok    |  142  |  87   |  38s
  us_msha_regulations.pdf  |  error |  —    |  —    |  —
```

---

## Known Limitations

| Issue | Affected | Notes |
|---|---|---|
| PDFs > 100 pages — Bedrock hard limit | Large regulatory documents | The pipeline respects this limit and processes up to 100 pages per document. Split documents exceeding this before upload |
| Sync invoke `read_timeout` on dense PDFs | CLI `lambda.invoke pdf dev --wait` | Timeout set to 600 s in the extractor; for very large batches prefer `--async` + `pdf-async-status` |
| EventBridge rule retries on Lambda errors | All PDFs | Check `pdf-async-status` for error details; re-trigger manually with `lambda.invoke pdf dev` |

---

## Rebuilding the Index from Scratch

```bash
# Full reindex: flush both indices and re-ingest all S3 data
uv run fab opensearch.reindex dev

# Or step by step:
uv run fab lambda.invoke-all csv dev --parallel   # re-ingest CSVs
uv run fab lambda.invoke-all pdf dev              # re-ingest PDFs
uv run fab opensearch.status dev                  # verify doc counts
```
