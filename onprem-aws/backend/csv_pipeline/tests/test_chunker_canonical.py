"""
Tests for canonical-aware Stage 3 serialization.

Requires pandas (skipped cleanly if absent, so it runs in SageMaker/CI):
    cd <repo-root>
    python -m pytest csv_pipeline/tests/test_chunker_canonical.py -v
    # or:
    python -m csv_pipeline.tests.test_chunker_canonical
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Importing the chunker pulls in pandas + boto3 (module-level). When those are
# absent (e.g. a bare dev box), skip every test cleanly instead of erroring -
# the suite is meant to run where the pipeline's deps are installed (SageMaker/CI).
try:
    import pandas as pd
    from csv_pipeline.tools.chunker_serializer import (
        _build_canonical_markers,
        _build_text,
        _safe_round,
    )
    _HAS_DEPS = True
except Exception as _exc:  # noqa: BLE001
    _HAS_DEPS = False
    _IMPORT_ERR = _exc


# ---------------------------------------------------------------------------
# _safe_round - never crashes on odd scalars (the numpy.bool_ bug)
# ---------------------------------------------------------------------------

def test_safe_round_handles_bool_and_numbers():
    if not _HAS_DEPS:
        print(f"SKIP (deps absent: {_IMPORT_ERR}): test_safe_round_handles_bool_and_numbers")
        return
    assert _safe_round(3.14159) == 3.142
    assert _safe_round(True) == 1.0          # bool -> float, no crash
    assert _safe_round("not a number") == "not a number"  # degrades gracefully
    import numpy as np
    # The exact scalar that crashed Stage 3 originally.
    assert _safe_round(np.bool_(True)) == 1.0


# ---------------------------------------------------------------------------
# _build_canonical_markers - domain-scoped absence + unmapped
# ---------------------------------------------------------------------------

def test_markers_are_domain_scoped():
    if not _HAS_DEPS:
        print(f"SKIP (deps absent): test_markers_are_domain_scoped")
        return
    descriptor = {
        "canonical": {
            "active_domains": ["tire", "shared"],
            "absent_fields": ["wear_pct", "burst_risk_score", "payload_tonnes", "gas_co"],
            "unknown_columns": ["vendor_blob"],
        }
    }
    m = _build_canonical_markers(descriptor)
    # tire-domain absences are reported...
    assert "wear_pct" in m["missing_canonical_fields"]
    assert "burst_risk_score" in m["missing_canonical_fields"]
    # ...but absences from domains the file never activated are NOT noise.
    assert "gas_co" not in m["missing_canonical_fields"]        # environmental_sensor
    assert "payload_tonnes" not in m["missing_canonical_fields"]  # load
    assert m["unmapped_columns"] == ["vendor_blob"]


def test_markers_empty_when_no_canonical_block():
    if not _HAS_DEPS:
        print("SKIP (deps absent): test_markers_empty_when_no_canonical_block")
        return
    m = _build_canonical_markers({})  # backward compatible
    assert m["missing_canonical_fields"] == []
    assert m["unmapped_columns"] == []


# ---------------------------------------------------------------------------
# _build_text - bool column does NOT crash, markers appear
# ---------------------------------------------------------------------------

def test_build_text_with_bool_column_does_not_crash():
    if not _HAS_DEPS:
        print("SKIP (deps absent): test_build_text_with_bool_column_does_not_crash")
        return

    chunk = pd.DataFrame({
        "equipment_id":   ["T1", "T1", "T2", "T2"],
        "anomaly_detected": [True, False, True, True],   # bool -> categorical
        "fuel_litres":    [10.0, 12.5, 9.0, 11.0],       # numeric metric
    })
    markers = {
        "missing_canonical_fields": ["payload_tonnes", "cycle_time_min"],
        "unmapped_columns": ["vendor_blob"],
        "active_domains": ["fleet", "shared"],
    }
    text = _build_text(
        chunk, 0, "C9/x.csv", "C9",
        entity_cols=["equipment_id"],
        metric_cols=["fuel_litres"],            # bool is NOT a metric here
        datetime_cols=[],
        cat_cols=["anomaly_detected"],          # bool routed as categorical
        date_range=None,
        markers=markers,
    )
    # No exception == the original numpy.bool_ crash is gone. Markers present:
    assert "Fields not tracked in this dataset:" in text
    assert "payload_tonnes" in text
    assert "Unmapped source columns" in text
    assert "vendor_blob" in text
    # bool column summarized as a category count, not a numeric round:
    assert "anomaly_detected" in text


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
