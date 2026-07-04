"""
s3_browser — Tool 7
Lists CSV files within an S3 prefix (folder).
In local_mode, scans sample_data/<folder>/ instead.
"""

from __future__ import annotations

from pathlib import Path

from config.settings import settings


def list_folder(folder: str, *, local_mode: bool = False) -> list[str]:
    """
    Return all CSV file paths within a folder, sorted alphabetically.

    Parameters
    ----------
    folder     : S3 prefix / local subfolder name, e.g. "C1"
    local_mode : if True, read from sample_data/<folder>/ instead of S3

    Returns
    -------
    List of relative file path strings, e.g. ["C1/events.csv", "C1/shifts.csv"]
    """
    if local_mode:
        return _list_local(folder)
    return _list_s3(folder)


def _list_s3(folder: str) -> list[str]:
    import boto3

    prefix = (settings.s3.prefix + folder).rstrip("/") + "/"
    s3 = boto3.client("s3", region_name=settings.s3.region)
    paginator = s3.get_paginator("list_objects_v2")

    keys: list[str] = []
    for page in paginator.paginate(Bucket=settings.s3.bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            if key.lower().endswith(".csv") and not key.endswith("/"):
                # Return key relative to the configured prefix so csv_loader
                # can reconstruct the full S3 key using settings.s3.prefix + path
                relative = key[len(settings.s3.prefix) :]
                keys.append(relative)

    return sorted(keys)


def _list_local(folder: str) -> list[str]:
    base = Path(settings.local_data_path) / folder
    if not base.exists():
        return []
    root = Path(settings.local_data_path)
    return sorted(
        str(p.relative_to(root)).replace("\\", "/") for p in base.rglob("*.csv")
    )
