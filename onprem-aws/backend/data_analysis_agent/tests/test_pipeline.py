"""
Pipeline smoke test.

Sends a folder name to FolderPipeline, runs it end-to-end — deterministic
schema discovery/dashboard assembly plus the per-file Strands agent on
Bedrock — and checks the resulting dashboard-sectioned JSON report. This
hits real Bedrock (schema_advisor's column-mapping call, and the per-file
agent's tool-use loop); see test_pipeline_unit.py for fast, offline,
mocked-agent tests of the safety-net wiring.

Run as pytest (integration marker):
    pytest tests/test_pipeline.py -v -m integration -s

Run as a script (folder name is optional, defaults to "C1"):
    python tests/test_pipeline.py
    python tests/test_pipeline.py C1
    python tests/test_pipeline.py C1 --s3          # use S3 instead of local data
    python tests/test_pipeline.py C1 --out out.json
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_analysis_agent.agent.pipeline import FolderPipeline

pytestmark = pytest.mark.integration

DEFAULT_FOLDER = "C1"
_SECTIONS = (
    "fleet",
    "maintenance",
    "kpis",
    "load_and_tonnage",
    "fuel",
    "gps_location",
    "safety",
)


# ---------------------------------------------------------------------------
# Pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report():
    """Run the pipeline once and share the result across all tests in this file."""
    local_mode = os.environ.get("USE_S3", "").lower() not in ("1", "true", "yes")
    pipeline = FolderPipeline(local_mode=local_mode)
    return pipeline.run(DEFAULT_FOLDER)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineOutput:
    def test_report_has_required_top_level_keys(self, report):
        for key in ("folder", "processed_at", "file_count", "overview", *_SECTIONS):
            assert key in report, f"Missing top-level key: '{key}'"

    def test_at_least_one_file_processed(self, report):
        files = report["overview"]["files"]
        assert report["file_count"] >= 1, (
            f"No CSV files found in folder '{DEFAULT_FOLDER}'. "
            "Ensure sample_data/C1/ exists and contains .csv files."
        )
        assert len(files) == report["file_count"]

    def test_each_file_entry_has_required_keys(self, report):
        required = ("path", "status", "rows", "columns", "errors")
        for f in report["overview"]["files"]:
            for key in required:
                assert key in f, f"File entry '{f.get('path')}' missing key '{key}'"

    def test_no_file_has_error_status(self, report):
        errors = [
            f["path"] for f in report["overview"]["files"] if f["status"] == "error"
        ]
        assert not errors, f"Files that failed completely: {errors}"

    def test_every_section_has_expected_shape(self, report):
        for name in _SECTIONS:
            section = report[name]
            assert isinstance(section["kpis"], list)
            assert isinstance(section["statistics"], dict)
            assert isinstance(section["outliers"], list)
            assert isinstance(section["trends"], list)
            assert isinstance(section["charts"], list)

    def test_charts_are_built_somewhere(self, report):
        all_charts = [c for name in _SECTIONS for c in report[name]["charts"]]
        assert len(all_charts) >= 1, (
            "Pipeline must always produce at least one chart spec across all "
            "sections (agent-built, or the deterministic fallback)."
        )
        for spec in all_charts:
            assert "chart_type" in spec
            assert "library" in spec
            assert "title" in spec

    def test_charts_contain_data(self, report):
        for name in _SECTIONS:
            for spec in report[name]["charts"]:
                payload = spec.get("data") or spec.get("cards")
                assert isinstance(payload, list) and len(payload) > 0, (
                    f"Chart '{spec.get('chart_type')}' in section '{name}' has no data."
                )

    def test_kpi_summary_matches_section_counts(self, report):
        by_section = report["overview"]["kpi_summary"]["by_section"]
        for name in _SECTIONS:
            assert by_section.get(name) == len(report[name]["kpis"])

    def test_report_is_json_serialisable(self, report):
        try:
            json.dumps(report, default=str)
        except (TypeError, ValueError) as exc:
            pytest.fail(f"Report is not JSON-serialisable: {exc}")

    def test_print_json_output(self, report, capsys):
        """Print the full report so it appears in pytest -s output."""
        print("\n" + "=" * 60)
        print(f"Pipeline report — folder: {report['folder']}")
        print(f"Processed at:  {report['processed_at']}")
        print(f"Files:         {report['file_count']}")
        print("=" * 60)
        for f in report["overview"]["files"]:
            print(
                f"\n  [{f['status'].upper()}] {f['path']}  "
                f"rows={f['rows']} cols={f['columns']}"
            )
            if f["errors"]:
                print(f"    errors: {list(f['errors'].keys())}")
        print("\nFull JSON:\n")
        print(json.dumps(report, indent=2, default=str))
        captured = capsys.readouterr()
        assert len(captured.out) > 0


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------


def _run_script():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run FolderPipeline and print JSON report."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=DEFAULT_FOLDER,
        help="S3 folder / local subfolder to process (default: C1)",
    )
    parser.add_argument(
        "--s3", action="store_true", help="Use S3 instead of local sample_data/"
    )
    parser.add_argument(
        "--out", metavar="PATH", help="Write report to this JSON file path"
    )
    args = parser.parse_args()

    pipeline = FolderPipeline(local_mode=not args.s3)
    report = pipeline.run(args.folder, output_path=args.out)

    print(json.dumps(report, indent=2, default=str))

    if args.out:
        print(f"\nReport saved → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    _run_script()
