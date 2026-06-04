"""
Pipeline smoke test.

Sends a folder name to FolderPipeline, runs the full analytics sequence,
and prints the resulting JSON report.

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.pipeline import FolderPipeline

pytestmark = pytest.mark.integration

DEFAULT_FOLDER = "C1"


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
        for key in ("folder", "processed_at", "file_count", "files"):
            assert key in report, f"Missing top-level key: '{key}'"

    def test_at_least_one_file_processed(self, report):
        assert report["file_count"] >= 1, (
            f"No CSV files found in folder '{DEFAULT_FOLDER}'. "
            "Ensure sample_data/C1/ exists and contains .csv files."
        )
        assert len(report["files"]) == report["file_count"]

    def test_each_file_has_required_keys(self, report):
        required = ("file_path", "status", "schema", "kpis",
                    "statistics", "insights", "charts", "errors")
        for f in report["files"]:
            for key in required:
                assert key in f, f"File '{f.get('file_path')}' missing key '{key}'"

    def test_no_file_has_error_status(self, report):
        errors = [f["file_path"] for f in report["files"] if f["status"] == "error"]
        assert not errors, f"Files that failed completely: {errors}"

    def test_schema_discovered_for_all_files(self, report):
        for f in report["files"]:
            schema = f.get("schema")
            assert schema is not None, f"No schema for '{f['file_path']}'"
            assert schema["row_count"] > 0
            # Datetime columns are optional — reference CSVs (e.g. locations,
            # lookup tables) may have none. Just assert the key is present.
            assert "datetime_columns" in schema
            assert isinstance(schema["datetime_columns"], list)

    def test_charts_are_built_not_empty(self, report):
        for f in report["files"]:
            charts = f.get("charts", [])
            assert len(charts) >= 1, (
                f"No charts built for '{f['file_path']}'. "
                "Pipeline must always produce at least one chart spec."
            )
            for spec in charts:
                assert "chart_type" in spec
                assert "library" in spec
                assert "title" in spec

    def test_charts_contain_data(self, report):
        for f in report["files"]:
            for spec in f.get("charts", []):
                payload = spec.get("data") or spec.get("cards")
                assert isinstance(payload, list) and len(payload) > 0, (
                    f"Chart '{spec.get('chart_type')}' in '{f['file_path']}' has no data."
                )

    def test_errors_dict_is_always_present(self, report):
        for f in report["files"]:
            assert isinstance(f.get("errors"), dict)

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
        for f in report["files"]:
            print(f"\n  [{f['status'].upper()}] {f['file_path']}")
            schema = f.get("schema") or {}
            print(f"    rows={schema.get('row_count')}  "
                  f"cols={schema.get('column_count')}  "
                  f"charts={len(f.get('charts', []))}")
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

    parser = argparse.ArgumentParser(description="Run FolderPipeline and print JSON report.")
    parser.add_argument("folder", nargs="?", default=DEFAULT_FOLDER,
                        help="S3 folder / local subfolder to process (default: C1)")
    parser.add_argument("--s3", action="store_true",
                        help="Use S3 instead of local sample_data/")
    parser.add_argument("--out", metavar="PATH",
                        help="Write report to this JSON file path")
    args = parser.parse_args()

    pipeline = FolderPipeline(local_mode=not args.s3)
    report = pipeline.run(args.folder, output_path=args.out)

    print(json.dumps(report, indent=2, default=str))

    if args.out:
        print(f"\nReport saved → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    _run_script()
