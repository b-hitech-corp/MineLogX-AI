"""
Frontend & API Layer Lambda — request router behind API Gateway (HTTP API v2).

Routes — LLM (POST, via Bedrock):
    POST /analyze   → data_analysis_agent.FleetAgent
    POST /chat      → rag_agent.BedrockRAGAgent

Routes — Data (GET, direct from S3 via csv_loader + kpi_engine, no LLM):
    GET  /health | /healthz
    GET  /fleet/assets
    GET  /kpis
    GET  /fuel/records
    GET  /fuel/trend
    GET  /maintenance/items
    GET  /maintenance/work-orders
    GET  /telemetry/gps
    GET  /telemetry/zones

All singletons are lazily initialized to survive warm invocations.
The handler supports both payload format v2 (HTTP API) and v1 (REST API).
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singletons — LLM agents
# ---------------------------------------------------------------------------

_fleet_agent = None
_rag_agent = None


def _get_fleet_agent():
    global _fleet_agent
    if _fleet_agent is None:
        from data_analysis_agent.agent.bedrock_orchestrator import FleetAgent

        _fleet_agent = FleetAgent()
    return _fleet_agent


def _get_rag_agent():
    global _rag_agent
    if _rag_agent is None:
        from rag_agent.bedrock_rag_agent import BedrockRAGAgent

        _rag_agent = BedrockRAGAgent()
    return _rag_agent


# ---------------------------------------------------------------------------
# Lazy singleton — telemetry DataFrame (loaded from S3 once per container)
# ---------------------------------------------------------------------------

_telemetry_df: pd.DataFrame | None = None


def _get_df() -> pd.DataFrame:
    """Load and cache the latest telemetry DataFrame from S3."""
    global _telemetry_df
    if _telemetry_df is not None:
        return _telemetry_df

    from data_analysis_agent.tools.s3_browser import list_folder
    from data_analysis_agent.tools.csv_loader import load_csv, get_dataframe

    # Try common prefixes in order of preference
    for folder in ("curated", "approved", "C1", "C2", ""):
        try:
            files = list_folder(folder)
        except Exception:
            continue
        if not files:
            continue
        dfs: list[pd.DataFrame] = []
        for fpath in files[
            :1
        ]:  # load 1 file — keeps cold-start < 60s and response < 6 MB
            try:
                load_csv(fpath)
                dfs.append(get_dataframe(fpath))
            except Exception:
                logger.warning("Skipping file %s", fpath, exc_info=True)
        if dfs:
            df = dfs[0]
            _telemetry_df = df.head(500)  # cap rows — Lambda response limit is 6 MB
            logger.info(
                "Loaded telemetry: %d rows (capped), folder=%s",
                len(_telemetry_df),
                folder,
            )
            return _telemetry_df

    logger.warning("No telemetry CSVs found — returning empty DataFrame")
    _telemetry_df = pd.DataFrame()
    return _telemetry_df


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _ok(body: dict | list | str, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
    }


def _err(message: str, status: int = 400) -> dict:
    return _ok({"error": message}, status)


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# DRY helpers for mapping DataFrame rows to dicts
# ---------------------------------------------------------------------------


def _fget(row: dict, *keys: str, default: float = 0.0) -> float:
    """Float with multiple key fallbacks."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return float(default)


def _sget(row: dict, *keys: str, default: str = "") -> str:
    """String with multiple key fallbacks."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            return str(v)
    return str(default)


def _rows(df: pd.DataFrame):
    """Yield (index, row_dict) pairs over a DataFrame."""
    return enumerate(r.to_dict() for _, r in df.iterrows())


# ---------------------------------------------------------------------------
# Mappers — Single Responsibility: CSV row → JSON dict for the frontend
# ---------------------------------------------------------------------------


def _map_asset_type(row: dict) -> str:
    t = _sget(row, "asset_type", "equipment_type").lower()
    if "excavat" in t:
        return "excavator"
    if "loader" in t:
        return "loader"
    if "dozer" in t:
        return "dozer"
    return "haul-truck"


def _map_fleet_status(row: dict) -> str:
    if _fget(row, "idle_hours") > _fget(row, "active_hours", default=1):
        return "idle"
    if _fget(row, "failure_count") > 0:
        return "maintenance"
    return "active"


def _map_gps_status(row: dict) -> str:
    if _fget(row, "speed_kph") > 5:
        return "moving"
    if _fget(row, "idle_hours") > 2:
        return "parked"
    return "idle"


def _map_fleet_asset(i: int, row: dict) -> dict:
    return {
        "id": _sget(row, "asset_id", "truck_id", default=str(i)),
        "name": _sget(row, "asset_name", "truck_name", default=f"Truck-{i}"),
        "type": _map_asset_type(row),
        "status": _map_fleet_status(row),
        "location": _sget(row, "zone", "location", default="Pit A"),
        "engineHours": _fget(row, "engine_on_hours", "operating_hours"),
        "fuelLevel": _fget(row, "fuel_level_pct", default=50),
        "speedKph": _fget(row, "speed_kph"),
        "loadTonnes": _fget(row, "payload_tonnes"),
        "cyclesCompleted": int(_fget(row, "cycle_count", "completed_cycles")),
        "fuelConsumptionLPH": _fget(row, "fuel_consumption_lph"),
    }


def _map_fuel_record(i: int, row: dict) -> dict:
    fuel = _fget(row, "fuel_litres", "fuel_used_litres")
    tonnes = max(_fget(row, "payload_tonnes", default=1), 0.1)
    return {
        "id": _sget(row, "asset_id", default=str(i)),
        "assetId": _sget(row, "asset_id", default=str(i)),
        "assetName": _sget(row, "asset_name", default=f"Truck-{i}"),
        "location": _sget(row, "zone", default="Pit A"),
        "fuelUsedLitres": fuel,
        "fuelEfficiencyLPT": round(fuel / tonnes, 2),
        "avgConsumptionLPH": _fget(row, "fuel_consumption_lph"),
        "sevenDayAvgLPH": _fget(row, "fuel_consumption_lph"),
        "anomaly": bool(row.get("anomaly", False)),
        "timestamp": _sget(
            row, "timestamp", default=datetime.now(timezone.utc).isoformat()
        ),
    }


def _map_maintenance_item(i: int, row: dict) -> dict:
    fp = _fget(row, "failure_probability")
    return {
        "id": f"M-{_sget(row, 'asset_id', default=str(i))}",
        "assetId": _sget(row, "asset_id", default=str(i)),
        "assetName": _sget(row, "asset_name", default=f"Truck-{i}"),
        "type": "Scheduled Maintenance",
        "status": "overdue" if fp > 0.7 else "scheduled",
        "priority": "critical" if fp > 0.7 else "medium",
        "scheduledDate": _sget(
            row,
            "scheduled_date",
            default=datetime.now(timezone.utc).date().isoformat(),
        ),
        "estimatedHours": _fget(row, "repair_time_hours", default=4),
        "failureProbability": round(fp, 2),
        "timeToFailureHours": _fget(row, "time_to_failure_hours", default=200),
    }


def _map_work_order(i: int, row: dict) -> dict:
    asset_id = _sget(row, "asset_id", default=str(i))
    return {
        "id": f"WO-{i + 1:04d}",
        "maintenanceId": f"M-{asset_id}",
        "assetId": asset_id,
        "title": f"WO - {_sget(row, 'asset_name', default=f'Asset-{i}')}",
        "description": "Auto-generated from telemetry",
        "status": "open",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def _map_gps_asset(i: int, row: dict) -> dict:
    asset_id = _sget(row, "asset_id", default=str(i))
    asset_name = _sget(row, "asset_name", default=f"Truck-{i}")
    return {
        "id": asset_id,
        "assetName": asset_name,
        "assetType": _map_asset_type(row),
        "x": _fget(
            row, "gps_x", "longitude", default=float((hash(asset_id) % 80) + 10)
        ),
        "y": _fget(
            row, "gps_y", "latitude", default=float((hash(asset_name) % 80) + 10)
        ),
        "zone": _sget(row, "zone", default="Pit A"),
        "speed": _fget(row, "speed_kph"),
        "heading": _fget(row, "heading_deg"),
        "status": _map_gps_status(row),
        "timestamp": _sget(
            row, "timestamp", default=datetime.now(timezone.utc).isoformat()
        ),
    }


# ---------------------------------------------------------------------------
# GET handlers — KISS: each handler does exactly one thing
# ---------------------------------------------------------------------------


def _h_fleet_assets(event: dict) -> dict:
    df = _get_df()
    return _ok([_map_fleet_asset(i, r) for i, r in _rows(df)])


def _h_kpis(event: dict) -> dict:
    from data_analysis_agent.tools.kpi_engine import calculate_kpi
    from data_analysis_agent.config.kpi_formulas import KPI_REGISTRY

    df = _get_df()
    if df.empty:
        return _ok([])

    # Load the DataFrame into the cache under a temporary key
    from data_analysis_agent.tools.csv_loader import _cache_set

    _cache_set("_live_", df)

    result = []
    for name, kdef in list(KPI_REGISTRY.items())[:12]:
        try:
            out = calculate_kpi("_live_", [name])
            kpi_val = out.get("kpis", {}).get(name, {})
            value = kpi_val.get("value", 0) if isinstance(kpi_val, dict) else 0
            target = getattr(kdef, "target", None) if hasattr(kdef, "target") else None
            if target and float(target) > 0:
                ratio = float(value) / float(target)
                status = (
                    "healthy"
                    if ratio >= 0.9
                    else "warning"
                    if ratio >= 0.7
                    else "critical"
                )
            else:
                status = "healthy"
            result.append(
                {
                    "id": kdef.name,
                    "label": kdef.description,
                    "value": round(float(value), 2)
                    if isinstance(value, (int, float))
                    else value,
                    "unit": kdef.unit,
                    "trend": "neutral",
                    "status": status,
                    "category": "fleet",
                }
            )
        except Exception:
            logger.warning("KPI failed: %s", name, exc_info=True)
    return _ok(result)


def _h_fuel_records(event: dict) -> dict:
    df = _get_df()
    return _ok([_map_fuel_record(i, r) for i, r in _rows(df)])


def _h_fuel_trend(event: dict) -> dict:
    df = _get_df()
    if (
        not df.empty
        and "timestamp" in df.columns
        and "fuel_consumption_lph" in df.columns
    ):
        df = df.copy()
        df["_h"] = df["timestamp"].astype(str).str[:13]
        grp = df.groupby("_h")["fuel_consumption_lph"].mean().reset_index()
        return _ok(
            [
                {
                    "hour": str(r["_h"])[-5:] + ":00",
                    "consumption": round(float(r["fuel_consumption_lph"]), 1),
                }
                for _, r in grp.iterrows()
            ]
        )
    avg = (
        float(df["fuel_consumption_lph"].mean())
        if not df.empty and "fuel_consumption_lph" in df.columns
        else 60.0
    )
    return _ok(
        [
            {
                "hour": f"{h:02d}:00",
                "consumption": round(avg * (0.8 + 0.4 * math.sin(h / 4)), 1),
            }
            for h in range(6, 22)
        ]
    )


def _h_maintenance_items(event: dict) -> dict:
    df = _get_df()
    return _ok([_map_maintenance_item(i, r) for i, r in _rows(df)])


def _h_work_orders(event: dict) -> dict:
    df = _get_df()
    return _ok([_map_work_order(i, r) for i, r in _rows(df)])


def _h_telemetry_gps(event: dict) -> dict:
    df = _get_df()
    return _ok([_map_gps_asset(i, r) for i, r in _rows(df)])


_PIT_ZONES = [
    {
        "id": "z1",
        "name": "Pit A",
        "type": "pit",
        "x": 10,
        "y": 10,
        "width": 30,
        "height": 25,
    },
    {
        "id": "z2",
        "name": "Dump B",
        "type": "dump",
        "x": 70,
        "y": 15,
        "width": 20,
        "height": 20,
    },
    {
        "id": "z3",
        "name": "Workshop",
        "type": "workshop",
        "x": 45,
        "y": 60,
        "width": 15,
        "height": 15,
    },
    {
        "id": "z4",
        "name": "Fuel Bay",
        "type": "fuel-bay",
        "x": 75,
        "y": 60,
        "width": 10,
        "height": 10,
    },
    {
        "id": "z5",
        "name": "Haul Road",
        "type": "haul-road",
        "x": 40,
        "y": 35,
        "width": 5,
        "height": 30,
    },
]


def _h_telemetry_zones(event: dict) -> dict:
    raw = os.environ.get("PIT_ZONES_JSON")
    zones = json.loads(raw) if raw else _PIT_ZONES
    return _ok(zones)


# Declarative GET router — single table, no if-chains (DRY)
_GET_ROUTES: dict[str, Callable[[dict], dict]] = {
    "/fleet/assets": _h_fleet_assets,
    "/kpis": _h_kpis,
    "/fuel/records": _h_fuel_records,
    "/fuel/trend": _h_fuel_trend,
    "/maintenance/items": _h_maintenance_items,
    "/maintenance/work-orders": _h_work_orders,
    "/telemetry/gps": _h_telemetry_gps,
    "/telemetry/zones": _h_telemetry_zones,
}

# ---------------------------------------------------------------------------
# POST handlers — LLM agents
# ---------------------------------------------------------------------------


def _handle_analyze(event: dict) -> dict:
    """POST /analyze — telemetry KPI / fleet analysis via FleetAgent."""
    body = _parse_body(event)
    question = (body.get("question") or body.get("message") or "").strip()
    if not question:
        return _err("'question' field is required")
    try:
        result = _get_fleet_agent().run(question)
        return _ok(
            {"success": True, "summary": result.summary, "charts": result.charts}
        )
    except Exception:
        logger.error("FleetAgent failed", exc_info=True)
        return _err("Analysis pipeline error", 502)


def _handle_chat(event: dict) -> dict:
    """POST /chat — compliance RAG Q&A via BedrockRAGAgent."""
    body = _parse_body(event)
    message = (body.get("message") or body.get("question") or "").strip()
    model = body.get("model")
    if not message:
        return _err("'message' field is required")
    try:
        response_json = _get_rag_agent().chat(message, model=model)
        return _ok(response_json)
    except Exception:
        logger.error("RAGAgent failed", exc_info=True)
        return _err("RAG pipeline error", 502)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict:  # noqa: ARG001
    # Supports HTTP API v2 (rawPath + requestContext.http.method)
    # and REST API v1 (path + httpMethod) — both payload formats
    method = (
        (event or {}).get("requestContext", {}).get("http", {}).get("method")
        or (event or {}).get("httpMethod", "GET")
    ).upper()
    path = (event or {}).get("rawPath") or (event or {}).get("path") or "/"
    # HTTP API v2 with named stage includes stage in rawPath: /dev/kpis → /kpis
    stage = (event or {}).get("requestContext", {}).get("stage", "")
    if stage and path.startswith(f"/{stage}/"):
        path = path[len(f"/{stage}") :]
    elif stage and path == f"/{stage}":
        path = "/"

    if path.rstrip("/") in ("", "/health", "/healthz"):
        return _ok(
            {
                "status": "ok",
                "service": "minelogx-api",
                "opensearch_host": os.environ.get("OPENSEARCH_HOST", ""),
                "guardrail_id": os.environ.get("GUARDRAIL_ID", ""),
            }
        )

    if method == "GET" and path in _GET_ROUTES:
        return _GET_ROUTES[path](event)

    if path == "/analyze" and method == "POST":
        return _handle_analyze(event)

    if path == "/chat" and method == "POST":
        return _handle_chat(event)

    return _err(f"No route for {method} {path}", 404)
