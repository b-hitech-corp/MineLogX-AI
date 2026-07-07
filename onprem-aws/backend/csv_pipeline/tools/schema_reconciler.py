"""
schema_reconciler.py - deterministic reconciliation of a CSV's columns against
the canonical mining-fleet schema (csv_pipeline/config/canonical_schema.py).

Given the raw column headers of a CSV, this resolves each header to a canonical
field using a three-tier matcher and emits a per-file resolved schema:

    1. exact canonical name
    2. strong alias  (`aliases`)      - specific names; also vote for domains
    3. weak alias    (`weak_aliases`) - generic catch-alls; only matched within
                                        the file's already-active domains

Domain inference uses ONLY tier-1/tier-2 (specific) hits, never weak aliases -
so an ambiguous generic column (e.g. a bare `temperature_c`) cannot drag in the
wrong domain and then win there. A weak alias matching several active-domain
fields is broken toward the domain with the most specific hits; an unbroken tie
at the same tier is quarantined, never guessed.

The reconciler embodies the schema's flexibility goal:
  * Columns that match nothing are returned as `unknown` - FLAGGED and EXCLUDED
    from the resolved schema, never force-mapped. Processing continues.
  * Same-tier ties are returned as `ambiguous` (quarantined) for an LLM/human
    pass - also never guessed.
  * Every canonical field gets a present/absent verdict in `presence_manifest`,
    so the consuming agent can tell "value is null" from "not tracked here".

Out of scope here (separate follow-ups): LLM semantic fallback for unknowns,
the intake-guard accept/reject/quarantine file verdict, EAV (_field/_value)
pivoting, wide->long unpivots, and per-file unit extraction from headers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from csv_pipeline.config.canonical_schema import CANONICAL_SCHEMA, is_ignorable

# ---------------------------------------------------------------------------
# Normalization + alias indices (built once at import)
# ---------------------------------------------------------------------------


def _norm(name: str) -> str:
    """Case / space / separator-insensitive key for alias matching."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# normalized canonical-name -> canonical field
_NAME_INDEX: dict[str, str] = {}
# normalized strong alias -> list of (canonical field, domain)
_STRONG_INDEX: dict[str, list[tuple[str, str]]] = {}
# normalized weak alias   -> list of (canonical field, domain)
_WEAK_INDEX: dict[str, list[tuple[str, str]]] = {}


def _build_indices() -> None:
    _NAME_INDEX.clear()
    _STRONG_INDEX.clear()
    _WEAK_INDEX.clear()
    for fname, spec in CANONICAL_SCHEMA.items():
        domain = spec.get("domain", "shared")
        _NAME_INDEX.setdefault(_norm(fname), fname)
        for alias in spec.get("aliases", []):
            _STRONG_INDEX.setdefault(_norm(alias), []).append((fname, domain))
        for alias in spec.get("weak_aliases", []):
            _WEAK_INDEX.setdefault(_norm(alias), []).append((fname, domain))


_build_indices()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ResolvedField:
    source_column: str  # raw header as it appeared in the CSV
    canonical_name: str  # resolved canonical field
    domain: str
    role: str
    dtype: str
    match_tier: str  # "name" | "strong" | "weak"
    expected_unit: str | None = None  # NON-authoritative hint from the schema


@dataclass
class ReconcileResult:
    # source column -> ResolvedField (only confidently resolved columns)
    resolved: dict[str, ResolvedField] = field(default_factory=dict)
    # canonical field -> "present" | "absent" (covers EVERY canonical field)
    presence_manifest: dict[str, str] = field(default_factory=dict)
    # raw columns that matched no canonical field (flagged, excluded)
    unknown_columns: list[str] = field(default_factory=list)
    # raw column -> candidate canonical fields (same-tier tie, quarantined)
    ambiguous_columns: dict[str, list[str]] = field(default_factory=dict)
    # raw columns dropped as transport/query mechanics (IGNORABLE_FIELDS)
    ignored_columns: list[str] = field(default_factory=list)
    # domains the file activated (via specific hits) + always "shared"
    active_domains: list[str] = field(default_factory=list)

    @property
    def present_fields(self) -> list[str]:
        return sorted(f for f, v in self.presence_manifest.items() if v == "present")

    @property
    def absent_fields(self) -> list[str]:
        return sorted(f for f, v in self.presence_manifest.items() if v == "absent")

    def to_dict(self) -> dict:
        """JSON-serializable view for embedding in the schema descriptor."""
        return {
            "canonical_resolution": {
                src: rf.canonical_name for src, rf in self.resolved.items()
            },
            "resolved_fields": {
                rf.canonical_name: {
                    "source_column": rf.source_column,
                    "domain": rf.domain,
                    "role": rf.role,
                    "dtype": rf.dtype,
                    "match_tier": rf.match_tier,
                    "expected_unit": rf.expected_unit,
                }
                for rf in self.resolved.values()
            },
            "canonical_field_presence": self.presence_manifest,
            "present_fields": self.present_fields,
            "absent_fields": self.absent_fields,
            "unknown_columns": self.unknown_columns,
            "ambiguous_columns": self.ambiguous_columns,
            "ignored_columns": self.ignored_columns,
            "active_domains": self.active_domains,
        }


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile(column_names: list[str]) -> ReconcileResult:
    """Resolve raw CSV headers against the canonical schema.

    Args:
        column_names: raw header strings (any casing/spacing/separators).

    Returns:
        ReconcileResult with resolved fields, a full presence manifest,
        and flagged unknown / ambiguous / ignored columns.
    """
    result = ReconcileResult()

    # 0) Drop transport/query mechanics before any matching.
    candidates: list[str] = []
    for col in column_names:
        if is_ignorable(col):
            result.ignored_columns.append(col)
        else:
            candidates.append(col)

    resolved_field_to_src: dict[str, str] = {}  # canonical field -> source col
    unresolved: list[str] = []

    # 1) + 2) Specific passes: exact canonical name, then strong alias.
    #     These establish the file's active domain set.
    domain_hits: dict[str, int] = {}  # domain -> count of specific hits

    def _claim(src: str, fname: str, tier: str) -> None:
        spec = CANONICAL_SCHEMA[fname]
        result.resolved[src] = ResolvedField(
            source_column=src,
            canonical_name=fname,
            domain=spec.get("domain", "shared"),
            role=spec.get("role", "metric"),
            dtype=spec.get("dtype", "string"),
            match_tier=tier,
            expected_unit=spec.get("expected_unit"),
        )
        resolved_field_to_src[fname] = src

    for col in candidates:
        key = _norm(col)
        # Tier 1: exact canonical name.
        # BUT a name that is ALSO a weak-alias token (e.g. `temperature_c`,
        # `pressure_psi`) is generic by the schema's own design and must be
        # domain-resolved via the weak pass - never exact-matched to its
        # like-named field, which would defeat tire-vs-engine disambiguation.
        if key in _NAME_INDEX and key not in _WEAK_INDEX:
            fname = _NAME_INDEX[key]
            _claim(col, fname, "name")
            domain_hits[CANONICAL_SCHEMA[fname].get("domain", "shared")] = (
                domain_hits.get(CANONICAL_SCHEMA[fname].get("domain", "shared"), 0) + 1
            )
            continue
        # Tier 2: strong alias
        if key in _STRONG_INDEX:
            cands = _STRONG_INDEX[key]
            uniq_fields = {f for f, _ in cands}
            if len(uniq_fields) == 1:
                fname = next(iter(uniq_fields))
                _claim(col, fname, "strong")
                dom = CANONICAL_SCHEMA[fname].get("domain", "shared")
                domain_hits[dom] = domain_hits.get(dom, 0) + 1
            else:
                # Same-tier tie among specific aliases -> quarantine, never guess.
                result.ambiguous_columns[col] = sorted(uniq_fields)
            continue
        unresolved.append(col)

    # Active domains = domains with >=1 specific hit, plus the universal "shared".
    active_domains = set(domain_hits) | {"shared"}
    result.active_domains = sorted(active_domains)

    # 3) Weak pass: generic catch-alls, only within active domains.
    for col in unresolved:
        key = _norm(col)
        if key not in _WEAK_INDEX:
            result.unknown_columns.append(col)
            continue

        # Keep only candidates whose domain the file actually activated.
        in_scope = [(f, d) for (f, d) in _WEAK_INDEX[key] if d in active_domains]
        # A field already filled by a specific match should not be re-claimed.
        in_scope = [(f, d) for (f, d) in in_scope if f not in resolved_field_to_src]

        if not in_scope:
            # Weak alias exists but its domain isn't active here -> unknown.
            result.unknown_columns.append(col)
            continue
        if len({f for f, _ in in_scope}) == 1:
            _claim(col, in_scope[0][0], "weak")
            continue

        # Multiple active-domain candidates: break toward the domain with the
        # most specific hits (most central to this file); never toward "shared".
        best_domain, best_score = None, -1
        tie = False
        for _, d in in_scope:
            if d == "shared":
                continue
            score = domain_hits.get(d, 0)
            if score > best_score:
                best_domain, best_score, tie = d, score, False
            elif score == best_score:
                tie = True
        winners = [f for f, d in in_scope if d == best_domain]
        if best_domain is not None and not tie and len(winners) == 1:
            _claim(col, winners[0], "weak")
        else:
            # Unbroken tie -> quarantine for review.
            result.ambiguous_columns[col] = sorted({f for f, _ in in_scope})

    # 4) Presence manifest for EVERY canonical field.
    for fname in CANONICAL_SCHEMA:
        result.presence_manifest[fname] = (
            "present" if fname in resolved_field_to_src else "absent"
        )

    return result
