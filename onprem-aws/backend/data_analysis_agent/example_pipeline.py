"""
Example: run the MineLogX analytics pipeline on an S3 folder.

Usage
-----
    python example_pipeline.py <folder>
    python example_pipeline.py C1
    python example_pipeline.py C1 --out report.json
"""

import json
import sys

from data_analysis_agent.agent.pipeline import FolderPipeline


def main(folder: str, output_path: str | None = None) -> None:
    print(f"Running pipeline on folder: '{folder}' ...\n", file=sys.stderr)

    pipeline = FolderPipeline()  # S3 mode — uses IAM credentials
    report = pipeline.run(folder, output_path=output_path)

    print(json.dumps(report, indent=2, default=str))

    if output_path:
        print(f"\nReport saved → {output_path}", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="S3 folder to analyse, e.g. C1")
    parser.add_argument(
        "--out", metavar="PATH", help="also write report to this JSON file"
    )
    args = parser.parse_args()

    main(args.folder, args.out)
