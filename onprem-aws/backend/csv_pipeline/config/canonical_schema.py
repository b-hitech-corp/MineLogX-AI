"""
Canonical schema for the mining-fleet analytics pipeline.

Each entry maps ONE true snake_case field name to its semantics. The schema
reconciler matches raw CSV headers against `aliases` (case / space / separator
insensitive) and rewrites them to the canonical name. Agents read
`description` for LLM field matching and use `kpi_links` to know which KPIs in
kpi_formulas.py a field can feed.

Units - the per-file contract
-----------------------------
A canonical field's UNIT is NOT a fixed property of the field; it is a property
of each FILE that supplies the field. The same `temperature` field may arrive
in C from one source and F from another - but never both within one file.
Therefore:

* The master schema below stays unit-agnostic. Each metric carries an optional
  `expected_unit` hint only as documentation/default - it is NOT authoritative
  and must never be assumed true for a given file.
* When the pipeline RESOLVES this schema against a concrete file, it must emit a
  per-file resolved schema in which every metric carries a `unit` string
  recorded EXACTLY as that file presents it (verbatim, no interpretation,
  no conversion). Example: a file declaring "Temp (C)" -> "unit": "C".
* If a file states no unit at all (e.g. the InfluxDB EAV export), record
  "unit": "missing". Never guess a unit. "missing" is a first-class,
  meaningful value, not an error.
* Normalization/conversion (C->F, psi->kPa, raw-ADC->ppm, ...) is the job of the
  layer that CONSUMES the resolved schema. The schema's only duty is to register
  the declared unit objectively.

Notes on this dataset
---------------------
* Raw CSV headers rarely match the variable names used inside
  kpi_formulas.py. The mapping lives entirely in `aliases`, and `kpi_links`
  connects the canonical field to the KPI(s) that consume it.
* `equipment_id` and `truck_id` are the same entity; likewise the location
  reference appears as `assigned_pit`, `zone`, and `location_id`.
* production_kpi_daily.csv is PRE-AGGREGATED - its columns are already-computed
  KPI outputs, so they are modelled as standalone metric fields with no
  `kpi_links` (they are not formula inputs).
* `required` only flags fields whose absence is worth surfacing; it is never a
  hard error.

Normalization rules (applied by the pipeline before KPI computation)
--------------------------------------------------------------------
* WIDE tire data: some sources give one column per corner
  (tire_pressure_FL_psi ... RR_psi). These map to the four
  `tire_pressure_<corner>_psi` canonical fields. To feed the long-format
  `pressure_psi` / `tire_position` model, unpivot the four corners into rows.
* DUAL GPS per cycle: haul-cycle sources may carry both a load point and a
  dump point on one row (gps_lat_load/lon_load + gps_lat_dump/lon_dump). These
  map to the four `gps_*_load` / `gps_*_dump` fields, distinct from the single
  `lat`/`lon` used by point-in-time GPS logs.
* CUMULATIVE engine hours: `engine_hours` is a lifetime running total, NOT an
  interval. KPIs that SUM operating_hours need the per-row DIFF of engine_hours
  (engine_hours.diff() within an equipment_id), not the raw value.
* LONG / EAV sources: some exports (e.g. InfluxDB annotated CSV) are
  entity-attribute-value - the real variable name lives as a STRING inside a
  field-name column (`_field`) and the number lives in a value column
  (`_value`). Header-based alias matching cannot see these. The pipeline must
  PIVOT `_field`/`_value` into named columns FIRST, then match each pivoted
  column's name against `aliases`. The 16 sensor fields below are the targets
  of that pivot for the InfluxDB source.
* DIRTY / NON-STANDARD data: files like the InfluxDB export carry no unit and
  may mix raw sensor counts with calibrated readings (a `_value` ceiling of
  4095 = 2**12 - 1 signals a raw 12-bit ADC count, not ppm). The schema cannot
  resolve this; it records unit: "missing" and the consuming layer must treat
  such values as uncalibrated until a unit/calibration is supplied. Never feed
  unit-"missing" values into unit-sensitive KPIs without an explicit override.

Code-facing concerns (NOT modelled as schema fields)
----------------------------------------------------
Some columns are transport/query mechanics with no analytical meaning and must
NOT be matched to canonical fields. They are not listed in CANONICAL_SCHEMA;
instead the consuming code should skip any header in IGNORABLE_FIELDS (and the
`#`-prefixed annotation rows of InfluxDB CSVs). Keeping this in code - not the
schema - avoids polluting the field space with non-data columns.

`role` values
-------------
entity      identifiers / dimensional keys that join tables
metric      numeric measures (KPI inputs or pre-computed KPI outputs)
datetime    timestamps and dates
categorical low-cardinality labels
metadata    descriptive / free-text / reference attributes

`domain` - file-level disambiguation
------------------------------------
Every field carries a single `domain` string. Domains:
  shared, fleet, asset_health, gps, load, safety,
  environmental_vehicle, environmental_sensor, tire, maximo, ai
`shared` fields (ids, timestamps, cross-domain flags) belong to every file.

Resolution contract: a file rarely belongs to exactly one domain - a fatigue
file carries operator + safety + behaviour fields; a maintenance file carries
maximo + safety (severity) fields; a GPS file carries gps + location fields.
So the matcher infers the SET of domains a file touches (its column mix /
source), then resolves each column against any field whose `domain` is in that
set OR `shared`. A column resolves if it matches a field in ANY of the file's
domains.

Collision protection is preserved because a file only opens the domains it
actually contains: a `tire` file that does NOT also contain asset_health
columns never opens asset_health, so `temperature_c` still resolves to tire
temperature, not engine temperature. Two fields may share an alias string ONLY
across domains that a single file would not normally co-activate; when in doubt
the intake guard quarantines rather than guessing.
  * a tire file (domains={tire}): `temperature_c` -> tire temperature
  * an asset_health file (domains={asset_health}): `temperature` -> engine/device temp
  * a fatigue file (domains={safety}, +shared): operator_id, fatigue_score,
    intervention_level, aggressive_driving_flag all resolve together
  * `device_id` -> equipment_id in machine files; a true external gateway id
    still resolves to sensor_node_id via its own aliases (`host`, `node_id`).
Fields that genuinely span domains (e.g. `severity` in both maximo and safety,
`lat`/`lon` used by gps and fleet) should be tagged with the domain where they
ORIGINATE; the multi-domain file resolution then picks them up wherever a file
co-activates that domain. Truly cross-cutting fields (ids, timestamps, flags,
engine_hours, anomaly_type, label, health_index) are tagged `shared`.

Alias precedence - strong vs weak
---------------------------------
Because a file may co-activate several domains, two fields can both plausibly
claim a GENERIC header like `temperature_c` or `pressure_psi` (e.g. tire vs
engine/system). Domain walls alone cannot break this tie once both domains are
open. So aliases have two tiers:
  * `aliases`        - strong/specific names. Matched FIRST. A strong match wins
                       outright (e.g. `tire_temp_c`, `engine_temperature_c`).
  * `weak_aliases`   - generic catch-alls (e.g. `temperature`, `pressure`).
                       Matched ONLY for columns still unresolved after the strong
                       pass, and only against fields in the file's active domains.
Matcher order per column: (1) exact canonical name, (2) strong `aliases`,
(3) `weak_aliases`. If two fields tie at the SAME tier, the intake guard
quarantines the column for review rather than guessing. This lets a tire file's
`temperature_c` bind to the tire field by its strong alias, while a machine file
with only a bare `temperature` still falls through to the engine/device field's
weak alias - no domain guess required.

Domain INFERENCE uses strong aliases only
-----------------------------------------
A file's active domain set is established ONLY by columns that match a strong,
domain-specific `alias`. Generic columns (those matchable only via a
`weak_alias`, e.g. a bare `temperature_c`/`pressure_psi`) DO NOT vote for a
domain - otherwise an ambiguous column would drag in the wrong domain and then
win there (the tire-vs-engine bug). So: tire-specific columns
(`tire_position`, `warning_level`, `tire_event_id`, ...) establish the `tire`
domain; only then does a generic `temperature_c` in that file bind to the tire
field. A machine file (rpm, vibration_m_s2, ...) establishes `asset_health`, and
its bare `temperature_c` binds to engine/device temp. When a weak alias matches
several active-domain fields, break the tie toward the domain with the most
strong-alias hits in this file (most central), and never toward `shared`.
"""

# ===========================================================================
# INTAKE GUARD - is this file canonical-eligible at all?
# ===========================================================================
# This spec is documentation for the pipeline stage that runs BEFORE any alias
# matching. Its job is NOT to map a file, but to decide whether the file should
# be mapped at all. The guiding principle is "expect the unexpected": we never
# assume a producer's structure matches our imagination, so the gate is
# CONSERVATIVE BY DEFAULT - a file must positively prove it is canonical-eligible
# to be accepted, and anything we don't clearly understand fails SAFE to
# quarantine rather than being force-mapped (which would ingest noise) or
# rejected (which would silently discard a novel-but-valid file).
#
# Three-way verdict - NEVER a plain pass/fail
# -------------------------------------------
#   "accept"      Positively matches a known-good structure. Auto-ingest.
#   "reject"      Positively junk OR positively bad structure. Quarantine + log.
#   "quarantine"  Everything else: unrecognized / ambiguous / low-confidence.
#                 Held for human-or-agent review, NEVER silently ingested.
#
# Every verdict MUST carry: {verdict, confidence (0-1), reasons[], signals{}}.
# The default verdict is "quarantine". accept and reject are both CLAIMS OF
# UNDERSTANDING and must be earned by positive signals; absence of a reason to
# accept is NOT a reason to reject.
#
# ACCEPT - only when one of these is positively true
# --------------------------------------------------
#   A1. HEADER-DRIVEN (default path): a real header row exists and a sufficient
#       share of headers resolve to canonical fields under the inferred file
#       `domain` (+ shared). Matching is done on HEADERS, not cell values.
#   A2. DECLARED EAV (long/key->value): the file explicitly declares a
#       field-name column paired with a value column (e.g. InfluxDB `_field` /
#       `_value`, marked by its #datatype/#group annotation rows). ONLY in this
#       case may cell VALUES be treated as field names and matched against
#       aliases - and only the declared key column's values.
#
# REJECT - only on POSITIVE bad signals (two kinds)
# -------------------------------------------------
#   R1. UNREADABLE / JUNK: empty file, unparseable, binary masquerading as CSV,
#       zero data rows, single constant column, etc.
#   R2. CONFIRMED PEER->PEER GRAPH / ASSOCIATION (the en-iot-30.csv case):
#       two or more columns drawing from ONE shared vocabulary (high token
#       overlap BETWEEN columns), a numeric weight that belongs to the
#       COMBINATION of tokens rather than measuring any one of them, and no
#       time/id anchor. This is a relationship/affinity table, not measurements.
#
# QUARANTINE - the safe default for everything else
# -------------------------------------------------
#   Q1. INFERRED-but-UNDECLARED EAV: looks long/key->value but has no explicit
#       field/value declaration. Do NOT auto-accept and do NOT treat cell
#       values as field names; hold for confirmation. (Prevents data tokens
#       like "humidity"/"temperature"/"pressure" from masquerading as columns.)
#   Q2. PARTIAL HEADER MATCH below the accept threshold.
#   Q3. NO HEADER and not positively classifiable as A2/R2.
#   Q4. Any novel shape we do not recognize. Unrecognized != invalid.
#
# Discipline that falls out of the above
# --------------------------------------
#   * Cell VALUES are matched as field names ONLY inside an A2 (declared EAV)
#     accept. Never as an opportunistic fallback. This is the structural fix for
#     the value-collision risk found in en-iot-30.csv (headerless; tokens
#     "humidity", "temperature", "pressure" collide with real aliases).
#   * Low confidence fails SAFE (quarantine), never to accept.
#   * Every quarantine is an opportunity for the schema to LEARN a new shape:
#     a reviewer confirms, and the new pattern is fed back as a future
#     accept/reject signal - rather than us pre-guessing every structure.
#
# The key discriminator is STRUCTURE, not the LOCATION of meaningful strings:
#   key->value (EAV)  -> the strings are transposed headers; match them (A2).
#   peer->peer (graph)-> the strings are related entities; reject them (R2).
# ===========================================================================

CANONICAL_SCHEMA: dict[str, dict] = {
    # =======================================================================
    # ENTITIES - identifiers and join keys
    # =======================================================================
    "equipment_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": [
            "equipment_id",
            "truck_id",
            "vehicle_id",
            "asset_id",
            "unit_id",
            "device_id",
            "machine_id",
        ],
        "description": "Unique identifier of a vehicle / haul truck or other tracked asset. In condition-monitoring files a per-machine `device_id` maps here (the device IS the machine, not an external sensor node).",
        "required": True,
        "kpi_links": [],
    },
    "operator_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["operator_id", "driver_id", "opr_id"],
        "description": "Unique identifier of the equipment operator / driver.",
        "required": False,
        "kpi_links": [],
    },
    "shift_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["shift_id"],
        "description": "Unique identifier of a specific shift instance (distinct from the Day/Night `shift` label).",
        "required": False,
        "kpi_links": [],
    },
    "location_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["location_id", "assigned_pit", "zone", "pit", "site_id", "zone_id"],
        "description": "Identifier of a mine location (pit, dump, crusher, fuel bay, maintenance bay) where an event occurs or an asset is assigned.",
        "required": False,
        "kpi_links": [],
    },
    "telemetry_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["telemetry_id"],
        "description": "Unique identifier of an equipment-health telemetry reading.",
        "required": False,
        "kpi_links": [],
    },
    "fuel_event_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["fuel_event_id"],
        "description": "Unique identifier of a fuel-management (refuel / burn) event.",
        "required": False,
        "kpi_links": [],
    },
    "gps_event_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["gps_event_id"],
        "description": "Unique identifier of a GPS movement-log sample.",
        "required": False,
        "kpi_links": [],
    },
    "cycle_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["cycle_id", "haul_cycle_id"],
        "description": "Unique identifier of a haul cycle (load -> haul -> dump -> return).",
        "required": False,
        "kpi_links": [],
    },
    "recommendation_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["recommendation_id"],
        "description": "Unique identifier of a predictive-maintenance recommendation.",
        "required": False,
        "kpi_links": [],
    },
    "fatigue_event_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["fatigue_event_id"],
        "description": "Unique identifier of an operator-fatigue detection event.",
        "required": False,
        "kpi_links": [],
    },
    "event_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["event_id", "safety_event_id"],
        "description": "Unique identifier of a safety or environmental event.",
        "required": False,
        "kpi_links": [],
    },
    "tire_event_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["tire_event_id", "tyre_event_id"],
        "description": "Unique identifier of a tire-pressure-monitoring reading.",
        "required": False,
        "kpi_links": [],
    },
    "sensor_node_id": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["sensor_node_id", "host", "node_id", "sensor_id"],
        "description": "Identifier of the physical sensor node / device that produced a reading (e.g. an ESP gateway). Distinct from equipment_id; a node may be fixed or asset-mounted.",
        "required": False,
        "kpi_links": [],
    },
    "measurement_source": {
        "dtype": "string",
        "role": "entity",
        "domain": "shared",
        "aliases": ["measurement_source", "_measurement", "measurement"],
        "description": "Source/grouping name a time-series backend attaches to a batch of readings (e.g. an InfluxDB measurement). May correspond to a site or logical stream; resolve to location_id only when confirmed.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # DATETIME
    # =======================================================================
    "timestamp": {
        "dtype": "datetime",
        "role": "datetime",
        "domain": "shared",
        "aliases": [
            "timestamp",
            "generated_time",
            "event_time",
            "reading_time",
            "_time",
            "time",
            "registered_in",
        ],
        "description": "Date and time at which an event or reading was recorded.",
        "required": True,
        "kpi_links": [],
    },
    "date": {
        "dtype": "datetime",
        "role": "datetime",
        "domain": "shared",
        "aliases": ["date", "report_date", "production_date"],
        "description": "Calendar date of a daily aggregated production record.",
        "required": False,
        "kpi_links": [],
    },
    "load_start_time": {
        "dtype": "datetime",
        "role": "datetime",
        "domain": "shared",
        "aliases": ["load_start_time", "load_timestamp"],
        "description": "Time loading began for a haul cycle.",
        "required": False,
        "kpi_links": [],
    },
    "haul_start_time": {
        "dtype": "datetime",
        "role": "datetime",
        "domain": "shared",
        "aliases": ["haul_start_time"],
        "description": "Time the loaded haul leg began for a cycle.",
        "required": False,
        "kpi_links": [],
    },
    "dump_time": {
        "dtype": "datetime",
        "role": "datetime",
        "domain": "shared",
        "aliases": ["dump_time", "dump_timestamp"],
        "description": "Time the load was dumped, ending the haul leg of a cycle.",
        "required": False,
        "kpi_links": [],
    },
    "commissioning_date": {
        "dtype": "datetime",
        "role": "datetime",
        "domain": "shared",
        "aliases": ["commissioning_date", "commission_date", "in_service_date"],
        "description": "Date the asset entered service.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # CATEGORICAL - asset / operator / location attributes
    # =======================================================================
    "equipment_type": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["equipment_type", "asset_type", "vehicle_type"],
        "description": "Class of equipment, e.g. Haul Truck.",
        "required": False,
        "kpi_links": [],
    },
    "manufacturer": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["manufacturer", "make", "oem"],
        "description": "Equipment manufacturer (e.g. CAT, Komatsu).",
        "required": False,
        "kpi_links": [],
    },
    "model": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["model", "model_name"],
        "description": "Manufacturer model designation of the asset.",
        "required": False,
        "kpi_links": [],
    },
    "operational_status": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["operational_status", "status", "asset_status"],
        "description": "Current operational state of the asset (e.g. Active, Down, Retired).",
        "required": False,
        "kpi_links": [],
    },
    "zone_type": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["zone_type", "location_type"],
        "description": "Functional type of a mine location (Pit, Dump, Crusher, Fuel, Maintenance).",
        "required": False,
        "kpi_links": [],
    },
    "geo_fence": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["geo_fence", "geofence", "geofence_id"],
        "description": "Geofence zone label associated with a mine location.",
        "required": False,
        "kpi_links": [],
    },
    "operator_name": {
        "dtype": "string",
        "role": "metadata",
        "domain": "fleet",
        "aliases": ["name", "operator_name", "driver_name"],
        "description": "Full name of the operator.",
        "required": False,
        "kpi_links": [],
    },
    "operator_role": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["role", "operator_role", "job_role"],
        "description": "Operator's job role (e.g. Driver).",
        "required": False,
        "kpi_links": [],
    },
    "shift": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["shift", "shift_name", "shift_type"],
        "description": "Work shift assignment (Day, Night).",
        "required": False,
        "kpi_links": [],
    },
    "certification_level": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["certification_level", "cert_level"],
        "description": "Operator certification tier (e.g. Level 1-3).",
        "required": False,
        "kpi_links": ["license_compliance_rate", "training_completion_rate"],
    },
    "tire_position": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "tire",
        "aliases": ["tire_position", "tyre_position", "wheel_position"],
        "description": "Wheel position of a tire reading (FL, FR, RL, RR).",
        "required": False,
        "kpi_links": [],
    },
    "warning_level": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "tire",
        "aliases": ["warning_level", "tire_warning_level", "alert_level"],
        "description": "Severity band of a tire condition (Normal, Warning, Critical).",
        "required": False,
        "kpi_links": [],
    },
    "severity": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "safety",
        "aliases": ["severity", "severity_level"],
        "description": "Severity classification of an event or recommendation (Low, Medium, High, Critical).",
        "required": False,
        "kpi_links": [],
    },
    "intervention_level": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "safety",
        "aliases": ["intervention_level"],
        "description": "Level of intervention triggered by a fatigue event (Low, Medium, Critical).",
        "required": False,
        "kpi_links": [],
    },
    "event_type": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "safety",
        "aliases": ["event_type", "safety_event_type"],
        "description": "Type of safety/environmental event (e.g. Speeding Violation, Near Miss, Harsh Braking Event, Unsafe Cornering).",
        "required": False,
        "kpi_links": [],
    },
    "issue_detected": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "maximo",
        "aliases": ["issue_detected", "issue"],
        "description": "Maintenance issue pattern identified by the predictive model.",
        "required": False,
        "kpi_links": [],
    },
    "recommended_action": {
        "dtype": "string",
        "role": "metadata",
        "domain": "maximo",
        "aliases": ["recommended_action", "action"],
        "description": "Recommended maintenance action text.",
        "required": False,
        "kpi_links": [],
    },
    "work_order_status": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "maximo",
        "aliases": ["work_order_status", "wo_status"],
        "description": "Lifecycle status of the work order tied to a recommendation (Draft, Approved, Scheduled).",
        "required": False,
        "kpi_links": ["defect_capture_rate", "work_order_backlog_ratio"],
    },
    "root_cause": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "maximo",
        "aliases": ["root_cause"],
        "description": "Diagnosed root cause of the detected issue.",
        "required": False,
        "kpi_links": [],
    },
    "corrective_action": {
        "dtype": "string",
        "role": "metadata",
        "domain": "safety",
        "aliases": ["corrective_action"],
        "description": "Corrective action assigned in response to a safety/environmental event.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - asset / location specs
    # =======================================================================
    "capacity_tons": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": [
            "capacity_tons",
            "capacity_tonnes",
            "max_payload_tonnes",
            "rated_capacity_t",
        ],
        "description": "Rated payload capacity of the asset in tonnes; reference for overload and payload-utilization checks.",
        "required": False,
        "kpi_links": ["overload_rate", "payload_utilization"],
    },
    "elevation": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["elevation", "elevation_m"],
        "description": "Elevation of a mine location in metres.",
        "required": False,
        "kpi_links": [],
    },
    "lat": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["lat", "latitude", "gps_lat"],
        "description": "Latitude coordinate of an event or location.",
        "required": False,
        "kpi_links": [],
    },
    "lon": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["lon", "lng", "longitude", "gps_lon"],
        "description": "Longitude coordinate of an event or location.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - equipment health telemetry
    # =======================================================================
    "engine_temp_c": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": [
            "engine_temp_c",
            "engine_temperature_c",
            "device_temp",
            "machine_temp",
        ],
        "description": "Engine / device temperature of a monitored machine, in degrees Celsius. Generic `temperature`/`temperature_c` resolve here only as a fallback (weak_aliases) and only when no domain-specific temperature field is active in the file.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "C",
        "weak_aliases": ["temperature_c", "temperature", "temp"],
    },
    "oil_pressure_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["oil_pressure_psi"],
        "description": "Engine oil pressure in psi.",
        "required": False,
        "kpi_links": [],
    },
    "vibration_index": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["vibration_index"],
        "description": "Normalised, unit-less vibration index from health telemetry.",
        "required": False,
        "kpi_links": [],
    },
    "vibration_m_s2": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["vibration_m_s2", "vibration_ms2", "vibration_accel", "vibration"],
        "description": "Physical vibration as acceleration in m/s^2 (distinct from the unit-less vibration_index).",
        "required": False,
        "kpi_links": [],
        "expected_unit": "m/s^2",
    },
    "rpm": {
        "dtype": "int",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["rpm", "engine_rpm", "shaft_rpm", "rotational_speed"],
        "description": "Rotational speed in revolutions per minute.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "rpm",
    },
    "system_pressure_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["system_pressure_psi", "manifold_pressure_psi"],
        "description": "System / manifold pressure of a monitored machine, in psi. Generic `pressure_psi`/`pressure` resolve here only as a fallback (weak_aliases) and only when no domain-specific pressure field is active in the file.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "psi",
        "weak_aliases": ["pressure_psi", "pressure"],
    },
    "coolant_level_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["coolant_level_pct", "coolant_pct"],
        "description": "Coolant level as a percentage of full.",
        "required": False,
        "kpi_links": [],
    },
    "health_score": {
        "dtype": "float",
        "role": "metric",
        "domain": "asset_health",
        "aliases": ["health_score", "asset_health_score"],
        "description": "Composite equipment-health score (0-100).",
        "required": False,
        "kpi_links": [],
    },
    "anomaly_detected": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "asset_health",
        "aliases": ["anomaly_detected", "anomaly_flag"],
        "description": "Whether an anomaly was flagged for this reading/event.",
        "required": False,
        "kpi_links": ["anomaly_detection_precision"],
    },
    "is_active": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "asset_health",
        "aliases": ["is_active", "active_flag", "device_active"],
        "description": "Whether the device/machine was in an active operating state for this reading (0/1).",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - fuel management
    # =======================================================================
    "fuel_litres": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": [
            "fuel_volume_l",
            "fuel_litres",
            "fuel_liters",
            "fuel_l",
            "fuel_consumed_l",
        ],
        "description": "Volume of fuel consumed or dispensed, in litres.",
        "required": False,
        "kpi_links": ["fuel_efficiency", "fuel_consumption_rate", "tonnes_per_litre"],
    },
    "fuel_rate_lph": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["fuel_rate_lph", "fuel_rate_l_per_hr"],
        "description": "Instantaneous fuel burn rate in litres per hour.",
        "required": False,
        "kpi_links": ["fuel_consumption_rate"],
    },
    "idle_burn_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["idle_burn_pct", "idle_fuel_pct"],
        "description": "Percentage of fuel burned while idling.",
        "required": False,
        "kpi_links": ["idle_emission_contribution", "idle_rate"],
    },
    "expected_burn_rate": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["expected_burn_rate", "target_burn_rate"],
        "description": "Expected/target fuel burn rate used as a baseline for anomaly detection.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - GPS movement
    # =======================================================================
    "speed_kmh": {
        "dtype": "float",
        "role": "metric",
        "domain": "gps",
        "aliases": [
            "speed_kmh",
            "speed_km_h",
            "speed",
            "speed_avg_kph",
            "avg_speed_kph",
        ],
        "description": "Instantaneous vehicle speed in km/h.",
        "required": False,
        "kpi_links": ["speed_compliance_rate"],
    },
    "heading": {
        "dtype": "float",
        "role": "metric",
        "domain": "gps",
        "aliases": ["heading", "bearing"],
        "description": "Compass heading in degrees (0-359).",
        "required": False,
        "kpi_links": [],
    },
    "geofence_violation": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "gps",
        "aliases": ["geofence_violation", "geofence_violation_flag"],
        "description": "Whether the sample violated a geofence boundary.",
        "required": False,
        "kpi_links": ["geofence_violation_rate"],
    },
    "idle_flag": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "gps",
        "aliases": ["idle_flag", "is_idle"],
        "description": "Whether the vehicle was idling at this sample.",
        "required": False,
        "kpi_links": ["idle_rate"],
    },
    "harsh_braking_flag": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "gps",
        "aliases": ["harsh_braking_flag", "harsh_brake_flag"],
        "description": "Whether a harsh-braking event occurred at this sample.",
        "required": False,
        "kpi_links": [],
    },
    "speeding_flag": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "gps",
        "aliases": ["speeding_flag", "is_speeding"],
        "description": "Whether the vehicle exceeded the speed limit at this sample.",
        "required": False,
        "kpi_links": ["speeding_rate", "speed_compliance_rate"],
    },
    # =======================================================================
    # METRICS - haul cycle tracking
    # =======================================================================
    "payload_tonnes": {
        "dtype": "float",
        "role": "metric",
        "domain": "load",
        "aliases": ["tonnage", "payload_tonnes", "payload_t", "load_tonnes"],
        "description": "Actual payload moved in a haul cycle, in tonnes.",
        "required": False,
        "kpi_links": [
            "haul_truck_productivity",
            "payload_utilization",
            "total_tonnes_moved",
            "tonnes_per_hour",
            "tonnes_per_litre",
            "payload_accuracy",
        ],
    },
    "ore_grade": {
        "dtype": "float",
        "role": "metric",
        "domain": "load",
        "aliases": ["ore_grade", "grade"],
        "description": "Ore grade of the hauled material.",
        "required": False,
        "kpi_links": [],
    },
    "cycle_time_min": {
        "dtype": "float",
        "role": "metric",
        "domain": "load",
        "aliases": ["cycle_duration_min", "cycle_time_min", "cycle_time"],
        "description": "Total duration of a haul cycle, in minutes.",
        "required": False,
        "kpi_links": ["mean_cycle_time", "queue_time_ratio"],
    },
    "target_duration_min": {
        "dtype": "float",
        "role": "metric",
        "domain": "load",
        "aliases": ["target_duration_min", "target_cycle_time_min"],
        "description": "Target/benchmark cycle duration in minutes.",
        "required": False,
        "kpi_links": [],
    },
    "variance_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "load",
        "aliases": ["variance_pct", "cycle_variance_pct"],
        "description": "Percentage deviation of actual cycle time from target.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - maintenance recommendations
    # =======================================================================
    "predicted_failure_window_hr": {
        "dtype": "float",
        "role": "metric",
        "domain": "maximo",
        "aliases": ["predicted_failure_window_hr", "failure_window_hr"],
        "description": "Predicted hours until failure if no action is taken.",
        "required": False,
        "kpi_links": ["mean_time_between_failures"],
    },
    # =======================================================================
    # METRICS - operator fatigue
    # =======================================================================
    "shift_hours_elapsed": {
        "dtype": "float",
        "role": "metric",
        "domain": "safety",
        "aliases": ["shift_hours_elapsed", "hours_into_shift"],
        "description": "Hours elapsed in the operator's shift at the time of the event.",
        "required": False,
        "kpi_links": [],
    },
    "fatigue_score": {
        "dtype": "float",
        "role": "metric",
        "domain": "safety",
        "aliases": ["fatigue_score"],
        "description": "Detected fatigue score for the operator at the event.",
        "required": False,
        "kpi_links": ["fatigue_event_rate"],
    },
    "fatigue_baseline": {
        "dtype": "float",
        "role": "metric",
        "domain": "safety",
        "aliases": ["fatigue_baseline"],
        "description": "Operator's baseline fatigue score for comparison.",
        "required": False,
        "kpi_links": [],
    },
    "microsleep_risk": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "safety",
        "aliases": ["microsleep_risk"],
        "description": "Whether a microsleep risk was detected.",
        "required": False,
        "kpi_links": [],
    },
    "reaction_delay_ms": {
        "dtype": "float",
        "role": "metric",
        "domain": "safety",
        "aliases": ["reaction_delay_ms"],
        "description": "Operator reaction delay in milliseconds.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - safety / environmental
    # =======================================================================
    "environmental_index": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_vehicle",
        "aliases": ["environmental_index", "env_index"],
        "description": "Composite environmental impact index for the event.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # METRICS - tire pressure monitoring
    # =======================================================================
    "pressure_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["tire_pressure_psi"],
        "description": "Tire pressure in psi. Generic `pressure_psi`/`pressure` resolve here only as a fallback within an already-active tire domain.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "psi",
        "weak_aliases": ["pressure_psi", "pressure"],
    },
    "temperature_c": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["tire_temp_c", "tire_temperature_c"],
        "description": "Tire temperature in degrees Celsius. Generic `temperature_c`/`temperature` resolve here only as a fallback within an already-active tire domain.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "C",
        "weak_aliases": ["temperature_c", "temperature", "temp"],
    },
    "wear_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["wear_pct", "tire_wear_pct"],
        "description": "Tire wear as a percentage.",
        "required": False,
        "kpi_links": [],
    },
    "burst_risk_score": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["burst_risk_score"],
        "description": "Modelled tire-burst risk score.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # OPERATIONAL METRICS - per-cycle / per-asset (mining_truck_fleet)
    # =======================================================================
    "idle_time_min": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["idle_time_min", "idle_minutes"],
        "description": "Minutes spent idling during a haul cycle.",
        "required": False,
        "kpi_links": ["idle_rate"],
    },
    "engine_hours": {
        "dtype": "float",
        "role": "metric",
        "domain": "shared",
        "aliases": ["engine_hours", "engine_hour_meter", "smu", "service_meter_hours"],
        "description": "Cumulative lifetime engine/service-meter hours of the asset. NOTE: a running total - take the per-row diff within an equipment_id to derive interval operating_hours for SUM-based KPIs.",
        "required": False,
        "kpi_links": [],
    },
    "days_since_maintenance": {
        "dtype": "int",
        "role": "metric",
        "domain": "shared",
        "aliases": ["days_since_maintenance", "days_since_last_service"],
        "description": "Number of days elapsed since the asset's last maintenance service.",
        "required": False,
        "kpi_links": [],
    },
    "health_index": {
        "dtype": "float",
        "role": "metric",
        "domain": "shared",
        "aliases": ["health_index"],
        "description": "Composite equipment-health index on a 0-1 scale (distinct from the 0-100 `health_score`).",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # WIDE-FORMAT TIRE PRESSURE (one column per corner)
    #   Unpivot to pressure_psi + tire_position for the long-format model.
    # =======================================================================
    "tire_pressure_fl_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["tire_pressure_fl_psi", "tyre_pressure_fl_psi"],
        "description": "Front-left tire pressure in psi (wide format).",
        "required": False,
        "kpi_links": [],
    },
    "tire_pressure_fr_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["tire_pressure_fr_psi", "tyre_pressure_fr_psi"],
        "description": "Front-right tire pressure in psi (wide format).",
        "required": False,
        "kpi_links": [],
    },
    "tire_pressure_rl_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["tire_pressure_rl_psi", "tyre_pressure_rl_psi"],
        "description": "Rear-left tire pressure in psi (wide format).",
        "required": False,
        "kpi_links": [],
    },
    "tire_pressure_rr_psi": {
        "dtype": "float",
        "role": "metric",
        "domain": "tire",
        "aliases": ["tire_pressure_rr_psi", "tyre_pressure_rr_psi"],
        "description": "Rear-right tire pressure in psi (wide format).",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # DUAL GPS POINTS PER CYCLE (load point + dump point on one row)
    # =======================================================================
    "gps_lat_load": {
        "dtype": "float",
        "role": "metric",
        "domain": "gps",
        "aliases": ["gps_lat_load", "load_lat", "lat_load"],
        "description": "Latitude of the loading point for a haul cycle.",
        "required": False,
        "kpi_links": [],
    },
    "gps_lon_load": {
        "dtype": "float",
        "role": "metric",
        "domain": "gps",
        "aliases": ["gps_lon_load", "load_lon", "lon_load"],
        "description": "Longitude of the loading point for a haul cycle.",
        "required": False,
        "kpi_links": [],
    },
    "gps_lat_dump": {
        "dtype": "float",
        "role": "metric",
        "domain": "gps",
        "aliases": ["gps_lat_dump", "dump_lat", "lat_dump"],
        "description": "Latitude of the dumping point for a haul cycle.",
        "required": False,
        "kpi_links": [],
    },
    "gps_lon_dump": {
        "dtype": "float",
        "role": "metric",
        "domain": "gps",
        "aliases": ["gps_lon_dump", "dump_lon", "lon_dump"],
        "description": "Longitude of the dumping point for a haul cycle.",
        "required": False,
        "kpi_links": [],
    },
    # =======================================================================
    # ZONE REFERENCES & ML COLUMNS (mining_truck_fleet)
    # =======================================================================
    "pit_zone": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["pit_zone"],
        "description": "Loading pit zone for a haul cycle (e.g. Zone-A-North); references a mine location.",
        "required": False,
        "kpi_links": [],
    },
    "dump_zone": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["dump_zone"],
        "description": "Dump zone for a haul cycle (e.g. Dump-1); references a mine location.",
        "required": False,
        "kpi_links": [],
    },
    "fault_code": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "fleet",
        "aliases": ["fault_code", "fault"],
        "description": "Diagnostic fault code and label raised for the asset (sparse; null when no fault).",
        "required": False,
        "kpi_links": [],
    },
    "anomaly_type": {
        "dtype": "categorical",
        "role": "categorical",
        "domain": "shared",
        "aliases": ["anomaly_type"],
        "description": "Classified anomaly category for a record (e.g. normal, excessive_idle, fatigue_risk, delayed_cycle, high_fuel_consumption, maintenance_overdue, tire_pressure_low).",
        "required": False,
        "kpi_links": ["anomaly_detection_precision"],
    },
    "label": {
        "dtype": "int",
        "role": "metric",
        "domain": "shared",
        "aliases": ["label", "target", "is_anomaly"],
        "description": "Binary supervised-learning ground-truth label (1 = anomalous, 0 = normal).",
        "required": False,
        "kpi_links": ["prediction_accuracy"],
    },
    # =======================================================================
    # AMBIENT ENVIRONMENTAL SENSORS (gas / air-quality / weather)
    #   Targets of the EAV pivot from time-series exports (e.g. InfluxDB).
    #   `expected_unit` is a NON-authoritative hint only. The real unit is
    #   recorded per-file at resolution time as `unit` (verbatim, or "missing").
    #   The InfluxDB example file declares NO units - every one resolves to
    #   `unit: "missing"`, and several values are raw 12-bit ADC counts (<=4095),
    #   not calibrated concentrations.
    # =======================================================================
    "ambient_temperature": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": [
            "temperature",
            "ambient_temperature",
            "ambient_temp",
            "temp",
            "air_temp",
        ],
        "description": "Ambient air temperature at the sensor node.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "humidity": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["humidity", "relative_humidity", "rh"],
        "description": "Ambient relative humidity at the sensor node.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "wind_speed": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["windspeed", "wind_speed"],
        "description": "Wind speed measured at the sensor node.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "wind_direction": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["winddirect", "wind_direction", "wind_dir", "wind_heading"],
        "description": "Wind direction measured at the sensor node (typically degrees).",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_co": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["co", "carbon_monoxide", "gas_co"],
        "description": "Carbon monoxide (CO) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_ch4": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["ch4", "methane", "gas_ch4"],
        "description": "Methane (CH4) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_lpg": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["lpg", "gas_lpg"],
        "description": "Liquefied petroleum gas (LPG) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_propane": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["propane", "gas_propane"],
        "description": "Propane sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_h2": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["h2", "hydrogen", "gas_h2"],
        "description": "Hydrogen (H2) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_h2s": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["h2s", "hydrogen_sulfide", "hydrogen_sulphide", "gas_h2s"],
        "description": "Hydrogen sulfide (H2S) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_nh3": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["nh3", "ammonia", "gas_nh3"],
        "description": "Ammonia (NH3) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_nh4": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["nh4", "ammonium", "gas_nh4"],
        "description": "Ammonium (NH4) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_no2": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["no2", "nitrogen_dioxide", "gas_no2"],
        "description": "Nitrogen dioxide (NO2) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_alcohol": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["alcohol", "gas_alcohol", "ethanol"],
        "description": "Alcohol (ethanol vapour) sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "gas_toluene": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["toluene", "toulene", "gas_toluene"],
        "description": "Toluene sensor reading. NOTE: commonly mis-spelled 'Toulene' in raw exports; both spellings alias here.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    "smoke": {
        "dtype": "float",
        "role": "metric",
        "domain": "environmental_sensor",
        "aliases": ["smoke", "smoke_level", "gas_smoke"],
        "description": "Smoke sensor reading.",
        "required": False,
        "kpi_links": [],
        "expected_unit": "missing",
    },
    # =======================================================================
    # CROSS-DOMAIN LINK FLAGS
    # =======================================================================
    "fatigue_linked": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "shared",
        "aliases": ["fatigue_linked", "fatigue_related"],
        "description": "Whether the event/reading is attributed to operator fatigue.",
        "required": False,
        "kpi_links": [],
    },
    "aggressive_driving_linked": {
        "dtype": "bool",
        "role": "categorical",
        "domain": "shared",
        "aliases": ["aggressive_driving_linked", "aggressive_driving_flag"],
        "description": "Whether the event/reading is attributed to aggressive driving behaviour.",
        "required": False,
        "kpi_links": ["unsafe_behaviour_rate"],
    },
    # =======================================================================
    # PRE-AGGREGATED DAILY KPI OUTPUTS (production_kpi_daily.csv)
    #   These are already-computed KPI values, not formula inputs.
    # =======================================================================
    "total_tonnage": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["total_tonnage", "total_tonnes"],
        "description": "Pre-aggregated total tonnage moved on the day (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "average_cycle_time": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["average_cycle_time", "avg_cycle_time"],
        "description": "Pre-aggregated mean haul cycle time for the day, in minutes (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "fuel_efficiency_daily": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["fuel_efficiency"],
        "description": "Pre-aggregated daily fuel efficiency value (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "idle_time_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["idle_time_pct"],
        "description": "Pre-aggregated percentage of time idling for the day (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "dispatch_accuracy_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["dispatch_accuracy_pct"],
        "description": "Pre-aggregated dispatch accuracy percentage for the day (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "equipment_availability_pct": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["equipment_availability_pct", "availability_pct"],
        "description": "Pre-aggregated fleet availability percentage for the day (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "mtbf_daily": {
        "dtype": "float",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["MTBF", "mtbf"],
        "description": "Pre-aggregated mean time between failures for the day, in hours (computed KPI output).",
        "required": False,
        "kpi_links": [],
    },
    "fatigue_incidents": {
        "dtype": "int",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["fatigue_incidents"],
        "description": "Count of fatigue incidents recorded on the day.",
        "required": False,
        "kpi_links": [],
    },
    "safety_events": {
        "dtype": "int",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["safety_events"],
        "description": "Count of safety events recorded on the day.",
        "required": False,
        "kpi_links": [],
    },
    "tire_alerts": {
        "dtype": "int",
        "role": "metric",
        "domain": "fleet",
        "aliases": ["tire_alerts", "tyre_alerts"],
        "description": "Count of tire alerts recorded on the day.",
        "required": False,
        "kpi_links": [],
    },
}


# ===========================================================================
# Code-facing: columns to skip, NOT canonical fields.
# These are transport/query mechanics from time-series exports and similar
# tooling. The consuming pipeline should drop any header matching these
# (case/separator-insensitive) before attempting alias resolution, and should
# also drop the leading '#'-annotation rows of InfluxDB-style annotated CSVs.
# Kept here rather than in CANONICAL_SCHEMA so non-data columns never compete
# for a canonical match.
# ===========================================================================
IGNORABLE_FIELDS: set[str] = {
    "result",  # InfluxDB: query result name (e.g. "mean")
    "table",  # InfluxDB: result table index
    "_start",  # InfluxDB: query window start bound
    "_stop",  # InfluxDB: query window stop bound
    "_result",
    "unnamed: 0",  # pandas index artifact from a leading comma column
    "",  # empty header (leading-comma column)
}


def is_ignorable(header: str) -> bool:
    """True if a raw column header is transport/query mechanics, not data."""
    import re

    norm = re.sub(r"[^a-z0-9]", "", header.lower())
    return any(re.sub(r"[^a-z0-9]", "", f.lower()) == norm for f in IGNORABLE_FIELDS)
