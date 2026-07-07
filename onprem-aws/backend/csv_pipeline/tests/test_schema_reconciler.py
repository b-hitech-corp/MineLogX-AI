"""
Unit tests for the deterministic schema reconciler.

Pure-Python (no AWS, no pandas) - runnable anywhere:
    cd <repo-root>
    python -m pytest csv_pipeline/tests/test_schema_reconciler.py -v
    # or without pytest:
    python -m csv_pipeline.tests.test_schema_reconciler
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from csv_pipeline.tools.schema_reconciler import reconcile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canon(result):
    """source -> canonical map for terse assertions."""
    return {src: rf.canonical_name for src, rf in result.resolved.items()}


# ---------------------------------------------------------------------------
# Exact + alias matching, case/space insensitivity
# ---------------------------------------------------------------------------


def test_exact_name_match():
    r = reconcile(["equipment_id", "timestamp", "payload_tonnes"])
    c = _canon(r)
    assert c["equipment_id"] == "equipment_id"
    assert c["timestamp"] == "timestamp"
    assert c["payload_tonnes"] == "payload_tonnes"


def test_alias_match_is_case_and_separator_insensitive():
    # The C2 bug: 'Timestamp' / 'Time Stamp' must resolve to canonical 'timestamp'.
    for header in ["Timestamp", "TIMESTAMP", "Time Stamp", "time-stamp"]:
        r = reconcile([header])
        assert _canon(r).get(header) == "timestamp", header


def test_strong_alias_maps_to_canonical():
    r = reconcile(["truck_id", "fuel_volume_l", "tonnage", "cycle_duration_min"])
    c = _canon(r)
    assert c["truck_id"] == "equipment_id"
    assert c["fuel_volume_l"] == "fuel_litres"
    assert c["tonnage"] == "payload_tonnes"
    assert c["cycle_duration_min"] == "cycle_time_min"


# ---------------------------------------------------------------------------
# Unknown columns: flagged + excluded, processing continues
# ---------------------------------------------------------------------------


def test_unknown_columns_are_flagged_not_mapped():
    r = reconcile(["equipment_id", "blast_hole_depth_m", "some_vendor_field"])
    c = _canon(r)
    assert c["equipment_id"] == "equipment_id"  # known still resolves
    assert "blast_hole_depth_m" in r.unknown_columns  # unknown flagged
    assert "some_vendor_field" in r.unknown_columns
    assert "blast_hole_depth_m" not in c  # excluded from resolution


def test_ignorable_columns_are_dropped_not_unknown():
    r = reconcile(["result", "table", "_start", "_stop", "timestamp"])
    for mech in ["result", "table", "_start", "_stop"]:
        assert mech in r.ignored_columns
        assert mech not in r.unknown_columns
    assert _canon(r)["timestamp"] == "timestamp"


# ---------------------------------------------------------------------------
# Presence manifest
# ---------------------------------------------------------------------------


def test_presence_manifest_covers_every_field():
    from csv_pipeline.config.canonical_schema import CANONICAL_SCHEMA

    r = reconcile(["equipment_id", "timestamp"])
    assert set(r.presence_manifest) == set(CANONICAL_SCHEMA)
    assert r.presence_manifest["equipment_id"] == "present"
    assert r.presence_manifest["timestamp"] == "present"
    # A field the file does not carry is explicitly absent, not missing.
    assert r.presence_manifest["payload_tonnes"] == "absent"
    assert "payload_tonnes" in r.absent_fields


# ---------------------------------------------------------------------------
# Domain-scoped weak-alias resolution (the tire-vs-engine disambiguation)
# ---------------------------------------------------------------------------


def test_weak_alias_binds_to_tire_in_a_tire_file():
    # tire_position is a strong, tire-specific alias -> activates 'tire'.
    # A bare 'temperature_c' (weak) must then bind to the TIRE temperature field.
    r = reconcile(["tire_event_id", "tire_position", "temperature_c", "pressure_psi"])
    c = _canon(r)
    assert c["temperature_c"] == "temperature_c"  # canonical tire temperature
    assert r.resolved["temperature_c"].domain == "tire"
    assert c["pressure_psi"] == "pressure_psi"
    assert r.resolved["pressure_psi"].domain == "tire"


def test_weak_alias_binds_to_engine_in_a_machine_file():
    # rpm/vibration_m_s2 are strong asset_health aliases -> activates asset_health.
    # A bare 'temperature_c' (weak) must bind to ENGINE temperature here.
    r = reconcile(["telemetry_id", "rpm", "vibration_m_s2", "temperature_c"])
    c = _canon(r)
    assert c["temperature_c"] == "engine_temp_c"
    assert r.resolved["temperature_c"].domain == "asset_health"


def test_weak_alias_without_active_domain_is_unknown():
    # 'temperature_c' alone activates no specific domain (only 'shared'),
    # so the weak alias has no active domain to bind to -> unknown, not guessed.
    r = reconcile(["timestamp", "temperature_c"])
    assert "temperature_c" in r.unknown_columns
    assert "temperature_c" not in _canon(r)


# ---------------------------------------------------------------------------
# Active domains + serialization
# ---------------------------------------------------------------------------


def test_active_domains_inferred_from_specific_hits_only():
    r = reconcile(["tire_event_id", "tire_position", "temperature_c"])
    assert "tire" in r.active_domains
    assert "shared" in r.active_domains  # always present
    # asset_health must NOT be activated by the generic temperature_c alone.
    assert "asset_health" not in r.active_domains


def test_to_dict_is_serializable_and_complete():
    import json

    r = reconcile(["equipment_id", "timestamp", "mystery_col"])
    d = r.to_dict()
    json.dumps(d)  # must not raise
    assert d["canonical_resolution"]["equipment_id"] == "equipment_id"
    assert "mystery_col" in d["unknown_columns"]
    assert d["canonical_field_presence"]["equipment_id"] == "present"


# ---------------------------------------------------------------------------
# Script entry point (run without pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fns = [
        v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)
    ]
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
