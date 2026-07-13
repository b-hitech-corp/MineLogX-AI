"""
analysis_report_serializer.py
=============================
Turn a FolderPipeline report (data_analysis_agent/agent/pipeline.py) into
OpenSearch documents for the analysis vector index.

Chunking is HIERARCHICAL (parent-child), per the plan (D1/D8):

  * Parent — one doc per (client, section) + an "overview" parent. Holds the
    full section `text` and the full structured `key_findings`. NOT embedded
    (parents are never matched by kNN); retrieved by parent_id at query time.
  * Child  — one doc per finding (each KPI, outlier, trend, ranking) within a
    section. Holds a small `text` and its single finding in `key_findings`.
    Children are the only docs that get embedded and kNN-searched.

Both levels carry the same client/section/provenance metadata so the RAG agent
can filter by client and collapse children back to their parent.

`key_findings` mirrors the numbers in `text` exactly (a single builder produces
both), so the LLM can be fed the structured values verbatim and never has to
re-read them out of prose.

Public API
----------
    render_report_chunks(report, *, content_signature, embed_model_id,
                         pipeline_version) -> list[AnalysisChunk]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Sections emitted by FolderPipeline (pipeline.py::_SECTION_KPIS) + overview.
_SECTIONS = (
    "overview",
    "fleet",
    "maintenance",
    "kpis",
    "load_and_tonnage",
    "fuel",
    "gps_location",
    "safety",
)


@dataclass
class AnalysisChunk:
    """One OpenSearch document (parent or child) before embedding/indexing."""

    chunk_level: str  # "parent" | "child"
    parent_id: str  # "{client_id}:{section}"
    client_id: str
    section: str
    text: str
    key_findings: dict
    kpi_names: list[str]
    source_files: list[str]
    content_signature: str
    signature_type: str = "etag"
    doc_type: str = "analysis"
    report_processed_at: str = ""
    embed_model_id: str = ""
    pipeline_version: str = ""
    # Populated by the ingestor for child docs only.
    embedding: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Finding extraction — pull structured key_findings from one report section
# ---------------------------------------------------------------------------


def _kpi_findings(section_data: dict, source_files: list[str]) -> list[dict]:
    findings: list[dict] = []
    for kpi in section_data.get("kpis") or []:
        name = kpi.get("name")
        if not name:
            continue
        if kpi.get("status") == "error":
            findings.append(
                {
                    "name": name,
                    "status": "error",
                    "error": kpi.get("error", "not computed"),
                    "source_files": source_files,
                }
            )
        elif "by_group" in kpi:
            findings.append(
                {
                    "name": name,
                    "status": "computed",
                    "grouped_by": kpi.get("grouped_by"),
                    "by_group": kpi.get("by_group"),
                    "unit": kpi.get("unit", ""),
                    "source_files": source_files,
                }
            )
        elif "value" in kpi:
            findings.append(
                {
                    "name": name,
                    "status": "computed",
                    "value": kpi.get("value"),
                    "unit": kpi.get("unit", ""),
                    "source_files": source_files,
                }
            )
    return findings


def _outlier_findings(section_data: dict, source_files: list[str]) -> list[dict]:
    out: list[dict] = []
    for o in section_data.get("outliers") or []:
        col = o.get("column")
        if not col:
            continue
        # samples may be scalars or dicts ({entity_id, <col>: value}); keep as-is,
        # capped, so exact example values survive without bloating the doc.
        out.append(
            {
                "column": col,
                "method": o.get("method", "iqr"),
                "outlier_count": o.get("outlier_count", 0),
                "samples": (o.get("samples") or [])[:5],
                "source_files": source_files,
            }
        )
    return out


def _trend_findings(section_data: dict, source_files: list[str]) -> list[dict]:
    out: list[dict] = []
    for t in section_data.get("trends") or []:
        col = t.get("value_column")
        if not col:
            continue
        out.append(
            {
                "value_column": col,
                "direction": t.get("direction"),
                "slope": t.get("slope"),
                "r_squared": t.get("r_squared"),
                "source_files": source_files,
            }
        )
    return out


def _headline_stats(section_data: dict, source_files: list[str]) -> list[dict]:
    out: list[dict] = []
    for col, stats in (section_data.get("statistics") or {}).items():
        if not isinstance(stats, dict):
            continue
        out.append(
            {
                "column": col,
                "mean": stats.get("mean"),
                "min": stats.get("min"),
                "max": stats.get("max"),
                "std": stats.get("std"),
                "source_files": source_files,
            }
        )
    return out


def _ranking_findings(section_data: dict, source_files: list[str]) -> list[dict]:
    """Extract top/bottom-N entity rankings from the section's BarChart(s).

    build_bar_chart (chart_spec_builder) stores {chart_type:"BarChart", data,
    x_key, y_keys}. Best-effort: skip anything that doesn't parse cleanly.
    """
    out: list[dict] = []
    for ch in section_data.get("charts") or []:
        if ch.get("chart_type") != "BarChart":
            continue
        data = ch.get("data") or []
        x_key = ch.get("x_key")
        y_keys = ch.get("y_keys") or []
        if not (data and x_key and y_keys):
            continue
        metric = y_keys[0]
        top = [
            {"entity_id": row.get(x_key), "value": row.get(metric)}
            for row in data[:10]
            if isinstance(row, dict)
        ]
        if top:
            out.append(
                {
                    "metric": metric,
                    "entity": x_key,
                    "order": "desc",
                    "top": top,
                    "source_files": source_files,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Text rendering — the same numbers as key_findings, for embedding + the LLM
# ---------------------------------------------------------------------------


def _fmt_kpi(f: dict) -> str:
    if f.get("status") == "error":
        return f"{f['name']} = NOT COMPUTED ({f.get('error', 'unknown')})"
    if "by_group" in f:
        return f"{f['name']} by {f.get('grouped_by')} = {f.get('by_group')} {f.get('unit', '')}".strip()
    return f"{f['name']} = {f.get('value')} {f.get('unit', '')}".strip()


def _child_text(client_id: str, section: str, kind: str, finding: dict) -> str:
    """Compact single-finding rendering (this is what gets embedded)."""
    prefix = f"{client_id} {section} —"
    if kind == "kpi":
        return f"{prefix} KPI {_fmt_kpi(finding)}"
    if kind == "outlier":
        return (
            f"{prefix} {finding['outlier_count']} outliers in {finding['column']} "
            f"({finding.get('method', 'iqr')})"
        )
    if kind == "trend":
        return (
            f"{prefix} trend of {finding['value_column']}: {finding.get('direction')} "
            f"(r²={finding.get('r_squared')}, slope={finding.get('slope')})"
        )
    if kind == "stat":
        return (
            f"{prefix} statistics for {finding['column']}: mean={finding.get('mean')}, "
            f"min={finding.get('min')}, max={finding.get('max')}"
        )
    if kind == "ranking":
        tops = ", ".join(
            f"{r.get('entity_id')}={r.get('value')}" for r in finding.get("top", [])[:5]
        )
        return f"{prefix} top {finding.get('entity')} by {finding.get('metric')}: {tops}"
    return f"{prefix} {finding}"


def _parent_text(
    client_id: str,
    section: str,
    processed_at: str,
    source_files: list[str],
    findings: dict,
) -> str:
    """Full section rendering (returned to the LLM after child→parent collapse)."""
    srcs = ", ".join(source_files) if source_files else "unknown sources"
    lines = [f"Client {client_id} — {section} section (computed {processed_at}, from {srcs})."]

    kpis = findings.get("kpis") or []
    if kpis:
        lines.append("KPIs: " + "; ".join(_fmt_kpi(k) for k in kpis) + ".")
    trends = findings.get("trends") or []
    if trends:
        lines.append(
            "Trends: "
            + "; ".join(
                f"{t['value_column']} {t.get('direction')} (r²={t.get('r_squared')})"
                for t in trends
            )
            + "."
        )
    outliers = findings.get("outliers") or []
    if outliers:
        lines.append(
            "Outliers: "
            + "; ".join(
                f"{o['outlier_count']} in {o['column']}" for o in outliers
            )
            + "."
        )
    rankings = findings.get("rankings") or []
    for r in rankings:
        tops = ", ".join(
            f"{x.get('entity_id')}={x.get('value')}" for x in r.get("top", [])[:5]
        )
        lines.append(f"Top {r.get('entity')} by {r.get('metric')}: {tops}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _client_source_files(report: dict) -> list[str]:
    """All successfully-processed source files for the client (from overview).

    The report does not attribute a KPI to a specific file, so a finding's
    provenance is the client's processed file set. If finer attribution becomes
    available later, populate per-finding source_files upstream.
    """
    files = (report.get("overview") or {}).get("files") or []
    return sorted(
        f.get("path")
        for f in files
        if f.get("path") and f.get("status") != "error"
    )


def render_report_chunks(
    report: dict,
    *,
    content_signature: str,
    embed_model_id: str,
    pipeline_version: str,
) -> list[AnalysisChunk]:
    """Render a FolderPipeline report into parent + child analysis chunks.

    Args:
        report: the dict returned by FolderPipeline.run(client).
        content_signature: representative signature for the client's inputs
            (stamped on every doc; the ledger keeps the per-file ETags).
        embed_model_id / pipeline_version: provenance stamped on every doc.

    Returns:
        A flat list of AnalysisChunk (parents + children) for one client.
    """
    client_id = report.get("folder") or ""
    processed_at = report.get("processed_at") or ""
    source_files = _client_source_files(report)

    chunks: list[AnalysisChunk] = []

    def _stamp(c: AnalysisChunk) -> AnalysisChunk:
        c.report_processed_at = processed_at
        c.embed_model_id = embed_model_id
        c.pipeline_version = pipeline_version
        return c

    for section in _SECTIONS:
        section_data = report.get(section)
        if not isinstance(section_data, dict):
            continue

        # Overview has no kpis/outliers/trends/statistics in the section sense;
        # render a compact summary parent only.
        if section == "overview":
            ov = section_data
            summary = ov.get("kpi_summary") or {}
            text = (
                f"Client {client_id} — overview (computed {processed_at}). "
                f"Files: {len(ov.get('files') or [])}, total rows: {ov.get('total_rows')}. "
                f"KPIs computed: {summary.get('total_computed')} "
                f"across sections {summary.get('by_section')}."
            )
            kf = {
                "overview": {
                    "total_rows": ov.get("total_rows"),
                    "file_count": len(ov.get("files") or []),
                    "kpi_summary": summary,
                    "source_files": source_files,
                }
            }
            chunks.append(
                _stamp(
                    AnalysisChunk(
                        chunk_level="parent",
                        parent_id=f"{client_id}:overview",
                        client_id=client_id,
                        section="overview",
                        text=text,
                        key_findings=kf,
                        kpi_names=[],
                        source_files=source_files,
                        content_signature=content_signature,
                    )
                )
            )
            continue

        kpis = _kpi_findings(section_data, source_files)
        outliers = _outlier_findings(section_data, source_files)
        trends = _trend_findings(section_data, source_files)
        stats = _headline_stats(section_data, source_files)
        rankings = _ranking_findings(section_data, source_files)

        # Skip sections with nothing computed.
        if not any([kpis, outliers, trends, stats, rankings]):
            continue

        section_findings = {
            "kpis": kpis,
            "outliers": outliers,
            "trends": trends,
            "headline_stats": stats,
            "rankings": rankings,
        }
        kpi_names = [k["name"] for k in kpis]
        parent_id = f"{client_id}:{section}"

        # Parent doc (full section, not embedded).
        chunks.append(
            _stamp(
                AnalysisChunk(
                    chunk_level="parent",
                    parent_id=parent_id,
                    client_id=client_id,
                    section=section,
                    text=_parent_text(
                        client_id, section, processed_at, source_files, section_findings
                    ),
                    key_findings=section_findings,
                    kpi_names=kpi_names,
                    source_files=source_files,
                    content_signature=content_signature,
                )
            )
        )

        # Child docs (one per finding, embedded).
        def _add_child(kind: str, finding: dict, single_key: str) -> None:
            chunks.append(
                _stamp(
                    AnalysisChunk(
                        chunk_level="child",
                        parent_id=parent_id,
                        client_id=client_id,
                        section=section,
                        text=_child_text(client_id, section, kind, finding),
                        key_findings={single_key: [finding]},
                        kpi_names=[finding["name"]] if kind == "kpi" else [],
                        source_files=finding.get("source_files") or source_files,
                        content_signature=content_signature,
                    )
                )
            )

        for f in kpis:
            _add_child("kpi", f, "kpis")
        for f in outliers:
            _add_child("outlier", f, "outliers")
        for f in trends:
            _add_child("trend", f, "trends")
        for f in stats:
            _add_child("stat", f, "headline_stats")
        for f in rankings:
            _add_child("ranking", f, "rankings")

    logger.info(
        "[analysis_serializer] client=%s → %d chunks (%d parents, %d children)",
        client_id,
        len(chunks),
        sum(1 for c in chunks if c.chunk_level == "parent"),
        sum(1 for c in chunks if c.chunk_level == "child"),
    )
    return chunks
