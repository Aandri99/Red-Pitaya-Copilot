# Qualification input contracts

All JSON files use UTF-8 objects. Keep them below one project root. Do not add
undocumented keys: the offline tools deliberately use strict key sets for
safety-critical records.

## Contents

1. General rules
2. Qualification request
3. Probe provenance and host-key evidence
4. Physical calibration and rating evidence
5. Generator driver contract
6. Generator driver evidence and reviews
7. Acquisition pre-state
8. Deployment and qualification authorization
9. Run observations

## 1. General rules

- Use real UTC as `YYYY-MM-DDTHH:MM:SSZ`.
- Use finite JSON numbers; never `NaN` or infinity.
- Express signal ceilings and ranges in Vpeak.
- Keep project-relative paths canonical and below the project root.
- Use only non-symlink regular files.
- Preserve source files after plan creation; validation re-hashes them.
- Never add credentials or private keys.
- Use strings that contain no control characters.
- Use a unique qualification ID containing letters, digits, dot, underscore or
  hyphen.

## 2. Qualification request

Top-level exact keys:

| Key | Requirement |
|---|---|
| `request_schema_version` | Integer `1` |
| `qualification_id` | Unique safe ID |
| `created_at_utc` | Real plan-creation UTC |
| `mode` | `active` or `read-only` |
| `probe_max_age_seconds` | Positive integer, at most one day |
| `purpose` | Concise operator intent |
| `topology` | Physical declaration below |
| `safety` | Limits and stop contract below |
| `driver_expectation` | Driver gate below |
| `acquisition` | Local/remote acquisition below |
| `sequence` | Deterministic sequence below |
| `acceptance` | Complete criteria list below |

### Topology

Use exact keys:

```text
generator_output_channel
generator_local_input
acquisition_remote_input
fanout_description
fanout_verified
shared_reference_verified
local_path
remote_path
```

Each input object contains:

```text
channel
range_volts_peak       positive
coupling               observed/configured label
termination_ohms       positive
range_evidence_path    project-relative input-range rating JSON
```

Each path object contains:

```text
attenuation_db                 non-negative measured/declared value
attenuation_uncertainty_db     non-negative
evidence_path                  project-relative calibration JSON
```

Both verification booleans must be true. Do not enter true until an operator
has inspected the physical fanout and signal reference.

### Safety

Use exact keys:

| Key | Requirement |
|---|---|
| `maximum_output_volts_peak` | Positive evidence-backed ceiling |
| `voltage_unit` | Exact string `Vpeak` |
| `output_limit_evidence_path` | Project-relative output-rating JSON |
| `deployment_maximum_output_to_vpeak_factor` | Positive conversion; must be exactly `1.0` because the deployment ceiling must be reviewed as Vpeak |
| `input_headroom_fraction` | Greater than zero and at most one |
| `one_shot_ceiling_fraction` | Greater than zero and at most 0.25 |
| `safe_output_state` | Exact string `disabled` |
| `emergency_stop_action` | Reviewed action; no secret |
| `emergency_stop_verification` | Independent readback/measurement |
| `single_writer_id` | Unique safe writer lease ID |
| `watchdog_timeout_seconds` | Positive and below maximum runtime |
| `maximum_runtime_seconds` | Positive total-run bound |
| `restore_acquisition_action` | Exact pre-state restore method |
| `restore_acquisition_verification` | Exact readback comparison method |

The planner checks the maximum output against both path headroom calculations
and the generator deployment contract.

### Driver expectation

Use exact keys:

```text
classification
expected_magic_ascii
expected_abi_major
minimum_abi_minor
expected_driver_version
approved_contract_sha256
required_capabilities
contract_review_path
advanced_review_path
```

For `pdg1`:

- `expected_magic_ascii` is `PDG1`;
- ABI fields are non-negative integers;
- expected driver version exactly matches the reviewed service/driver build;
- approved SHA-256 is the exact contract-file hash;
- contract review path names the independent release review JSON;
- advanced review path is null;
- required capabilities list every feature used.

For `approved-advanced`:

- use exact observed/approved magic and ABI;
- use exact contract SHA-256;
- set both `contract_review_path` and `advanced_review_path` to separate
  project-relative review JSON files.

For `unknown-read-only`:

- set all expected magic/ABI/hash/review fields, including both review paths,
  to null;
- set expected driver version to null;
- set required capabilities to an empty array;
- use request mode `read-only`.

An active plan also requires the contract capabilities:

```text
event-counter
safe-stop
single-writer
watchdog
```

### Acquisition

Use exact keys:

```text
local
remote
baseline
restore_required
counter_policy
```

Each local/remote endpoint contains:

| Key | Requirement |
|---|---|
| `sample_rate_hz` | Positive actual configuration |
| `record_samples` | Integer at least 2 |
| `pretrigger_samples` | Non-negative and below record length |
| `trigger_mode` | Exact configured method |
| `sample_units` | Exact units, normally established by the API/calibration |

Baseline contains positive `duration_seconds` and positive integer
`minimum_records`. `restore_required` must be true.

Counter policy contains:

- `counter_width_bits`: integer from 8 through 128;
- `reset_semantics`: exact contract behavior;
- `rollover_method`: reviewed method used without uncontrolled output.

### Sequence

Use:

```text
deterministic_seed
one_shot
modes
```

For active mode, one-shot contains exact keys:

```text
step_id
mode_name
amplitude_volts_peak
pulse_width_seconds
polarity
capture_window_seconds
expected_emitted_count
```

`expected_emitted_count` must be one. Amplitude must be no greater than:

```text
maximum_output_volts_peak × one_shot_ceiling_fraction
```

Each mode adds:

```text
duration_seconds
repetition_count
```

Every amplitude and time is positive. Polarity is `positive`, `negative` or
`bipolar`. Step IDs are unique. At least one mode must have amplitude strictly
above the one-shot, while remaining below the maximum.

For read-only mode, set one-shot to null and modes to an empty array.

### Acceptance

`acceptance` contains only `criteria`, an array with exactly one overall
criterion for every required metric:

```text
metric
unit
minimum
maximum
allowed_values
```

For numeric metrics, use finite minimum and/or maximum and set
`allowed_values` null. For categorical metrics, set numeric limits null and
provide a non-empty unique allowed-values array.

Required metrics:

```text
emitted_count
local_detected_count
remote_detected_count
local_delay_seconds
remote_delay_seconds
local_gain_ratio
remote_gain_ratio
local_correlation
remote_correlation
local_polarity
remote_polarity
local_width_seconds
remote_width_seconds
local_energy_volt2_seconds
remote_energy_volt2_seconds
local_ringing_hz
remote_ringing_hz
local_phase_degrees
remote_phase_degrees
detector_dead_time_seconds
local_data_loss_fraction
remote_data_loss_fraction
local_drop_count
remote_drop_count
counter_reset_behavior
counter_rollover_behavior
```

Derive limits from the system requirement, calibration and test objective. Do
not copy generic limits.

## 3. Probe provenance and host-key evidence

Supply the raw probe, matching target manifest and output from the current
deployment skill's `analyze_probe.py` for each role. The planner runs that
analyzer again and requires byte-for-byte equivalent JSON semantics with no
warnings. A handwritten normalized report or a coordinated replacement is not
accepted.

The qualification planner consumes and hash-binds:

- report kind/version/compatibility/errors/warnings;
- connection host, actual IP, SSH username and verified fingerprint;
- hostname, machine ID, serial and MAC addresses;
- exact role;
- observed and expected OS/model;
- capture time and source.

The plan separately hash-binds the raw probe, target manifest and normalized
report. The active deployment plan must bind the same manifest and normalized
report hashes and must pass the sibling deployment validator with the project
root supplied.

Use two different reports and identities. Resolve every warning.

For each board, also provide an independent host-key verification JSON with
exact keys:

```text
host_key_evidence_schema_version     1
role                                 generator or acquisition
captured_at_utc
host
address
algorithm
ssh_host_key_sha256
verification_method
verified_by
verification_reference
result                               verified
```

It must be fresh and match the raw/normalized connection exactly.

## 4. Physical calibration and rating evidence

Every file is project-relative, hash-bound and semantic, not a free-text
reference.

The output-limit JSON uses:

```text
evidence_schema_version                         1
kind                                            output-limit-rating
board_role                                      generator
channel
maximum_output_volts_peak
voltage_unit                                    Vpeak
deployment_maximum_output_unit                  Vpeak
deployment_maximum_output_to_vpeak_factor       1.0
source
reviewed_at_utc
```

Each input-range JSON uses:

```text
evidence_schema_version     1
kind                        input-range-rating
path_role                   local or remote
channel
range_volts_peak
voltage_unit                Vpeak
coupling
termination_ohms
source
reviewed_at_utc
```

Each path-calibration JSON uses:

```text
evidence_schema_version         1
kind                            signal-path-calibration
path_role                       local or remote
attenuation_db
attenuation_uncertainty_db
voltage_unit                    Vpeak
measurement_method
instrument_reference
measured_at_utc
```

All repeated values must exactly match the request.

## 5. Generator driver contract

The contract has an exact top-level shape:

```text
schema_version
namespace
magic_ascii
abi_major
abi_minor
register_base
address_span
capabilities
registers
```

`register_base`, `address_span` and every offset are `0x...` hexadecimal,
32-bit aligned and within a positive aligned span. Each register has exact
keys `name`, `offset`, `access`, `role`; access is `ro`, `rw`, `wo` or `w1c`.
Names, roles and offsets are unique. An active contract requires these semantic
roles:

```text
magic
abi-version
control
status
watchdog-timeout
watchdog-kick
event-counter-low
event-counter-high
```

Use non-negative ABI integers and a unique capability list. The exact contract
file hash is the driver identity boundary.

Do not place a register base or span in the qualification request. Read it from
this exact contract.

## 6. Generator driver evidence and reviews

Use exact keys:

```text
driver_evidence_schema_version
captured_at_utc
source
board_machine_id
ssh_host_key_sha256
read_only_probe
output_enabled_readback
writer_owner
contract
```

Version is `1`. The evidence must be fresh, read-only and bound to the exact
generator probe. `output_enabled_readback` must be false. `writer_owner` must be
null or the requested writer ID.

Contract readback contains exact keys:

```text
magic_ascii
abi_major
abi_minor
register_contract_sha256
register_base
address_span
capabilities
driver_version
```

Every field must equal the actual contract summary. Do not change evidence to
match a contract; investigate the mismatch.

Every active classification, including `pdg1`, requires an independent
contract review:

```text
review_schema_version                  1
classification                        pdg1 or approved-advanced
contract_sha256
magic_ascii
abi_major
abi_minor
driver_version
approved_capabilities
independent_from_qualification_request true
reviewed_at_utc
reviewer_identity
review_reference
```

The review must match the contract and expectation exactly.

An `approved-advanced` classification additionally requires the separate
advanced review:

Use exact keys:

```text
review_schema_version       1
classification              approved-advanced
contract_sha256
magic_ascii
abi_major
reviewed_at_utc
reviewer_identity
review_reference
```

The review is approval evidence for the exact advanced contract, not permission
to energize output. Physical-output permission still comes from the deployment
skill.

## 7. Acquisition pre-state

Use exact keys:

```text
state_schema_version        1
captured_at_utc
capture_method
generator_machine_id
acquisition_machine_id
generator_input_state
acquisition_input_state
restore_verification_method
```

Each input state has exact keys:

```text
state_format_version       1
channel
enabled
decimation
trigger_source
trigger_level_volts
trigger_delay_samples
gain_mode
coupling
termination_ohms
sample_units
```

Channels must equal the topology. A post-run restore artifact must repeat both
objects exactly; an empty or partial state is rejected.

## 8. Deployment and qualification authorization

Use exact keys:

```text
authorization_bundle_schema_version     1
source_skill                            deploy-red-pitaya-project
generator_plan                          project-relative path
acquisition_plan                        project-relative path
qualification_binding                   project-relative path
```

Do not copy receipt values into this bundle. Point to the final deployment
plans. The qualification planner invokes the sibling deployment validator with
the project root and rejects every error or warning before checking immutable
cores, identities, gates and referenced authorization files.

The qualification-binding JSON has exact keys:

```text
qualification_authorization_schema_version      1
source_skill                                    deploy-red-pitaya-project
result                                          authorized
qualification_id
request_sha256
generator_deployment_id
generator_plan_core_sha256
generator_output_receipt_sha256
acquisition_deployment_id
acquisition_plan_core_sha256
maximum_output_volts_peak
voltage_unit                                    Vpeak
single_use_nonce
recorded_at_utc
expires_at_utc
approver_identity
authorization_reference
```

It is recorded after the request, no more than `probe_max_age_seconds` later,
and expires no more than one hour after recording. Use a unique nonce. It
scopes an existing deployment authorization; it does not replace any
deployment gate. At execution, exclusive-create a run record binding the final
plan SHA-256 to that nonce. The active run must start inside the authorization
window.

## 9. Run observations

Use exact top-level keys:

```text
observation_schema_version
qualification_id
plan_sha256
started_at_utc
ended_at_utc
executor_id
writer_id
boards
driver_gate
authorization_bundle_sha256
authorization_single_use_nonce
timeline
waveforms
measurements
integrity
counter_checks
safety
artifacts
notes
```

### Board and driver repetition

Repeat both board machine IDs and SSH fingerprints observed at execution.

Repeat driver classification, contract SHA-256, magic, ABI and access decision.
These must equal the immutable plan.

Use the qualification plan's authorization-bundle file SHA-256 for active mode
or null for read-only mode.

Repeat the plan's single-use nonce for active mode or null for read-only mode.

### Timeline

Include exactly one row for every plan phase, in exact order:

```text
ordinal
step_id
status                       passed, failed or skipped
started_at_utc
ended_at_utc
emergency_stop_available
```

Times cannot overlap or move backward. The baseline phase must finish before
the first output-allowed phase.

### Waveforms

Include exactly:

| Channel key | Mandatory label |
|---|---|
| `generator-preview` | `reconstructed` |
| `generator-local-input` | `measured` |
| `acquisition-remote-input` | `measured` |

Each row has `channel`, `label` and `artifact`. Artifact is an exact file
binding:

```text
path
sha256
size_bytes
```

### Measurements

Each row contains:

```text
metric
scope
value
unit
method
provenance
```

Provide one `overall` row for every required metric. Additional per-step rows
are allowed. Unit must equal the matching criterion.

Use provenance:

- `measured`: direct acquisition or authoritative hardware counter;
- `derived`: calculation from measured data;
- `reconstructed`: generated from settings rather than digitized data.

Emitted/detected/drop counts must be `measured`.

### Integrity

Use exact keys:

```text
emitted_count
local_detected_count
remote_detected_count
local_drop_count
remote_drop_count
local_data_loss_fraction
remote_data_loss_fraction
local_sequence_gaps
remote_sequence_gaps
fifo_high_water
fifo_capacity
dead_time_seconds
```

Counts/gaps/FIFO values are non-negative integers. Fractions are zero through
one. High-water cannot exceed capacity. Linked values must equal their overall
measurement rows.

### Counter checks

Provide `reset` and `rollover`. Each contains:

```text
behavior
before
after
epoch_before
epoch_after
counter_width_bits
method
passed
```

Width must match the plan. Behavior must equal the categorical measurement.
`passed` is evidence from the described method; the reporter also evaluates the
categorical acceptance criterion.

### Safety

Use exact keys:

```text
baseline_completed_before_output
output_disabled_during_baseline
emergency_stop_available_all_phases
watchdog_armed
watchdog_timeout_seconds
maximum_commanded_output_volts_peak
safe_stop_invoked
output_disabled_after
single_writer_preserved
runtime_seconds
acquisition_restored
restored_state_sha256
```

The reporter validates runtime, voltage and restoration hash. It records false
safety booleans as failed gates rather than hiding the run.

For read-only mode, commanded Vpeak is zero, watchdog is not armed and driver
safe-stop is not invoked.

### Additional artifacts and notes

Each artifact contains `kind`, `provenance` and a `file` binding. Artifact kinds
are unique. Include these mandatory measured artifacts:

```text
command-audit-log
counter-snapshots
safety-readbacks
acquisition-restore-readback
integrity-diagnostics
```

Active runs also require `single-use-run-record`.

`acquisition-restore-readback` must be JSON with:

```text
restore_evidence_schema_version     1
qualification_id
generator_machine_id
acquisition_machine_id
captured_at_utc
generator_input_state
acquisition_input_state
comparison_result                   exact-match
```

Both state objects must exactly equal the pre-state.

`single-use-run-record` must be exclusively created before live actions and
contain:

```text
run_record_schema_version     1
qualification_id
plan_sha256
single_use_nonce
started_at_utc
executor_id
writer_id
creation_method               exclusive-create
```

For read-only mode, the command audit proves that no write command occurred.
Keep any additional raw logs, calibration records and analysis outputs with
clear unique kinds.

Notes are concise safe strings. Do not use notes to override a failed gate.
