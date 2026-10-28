# Single-board qualification input contracts

Contract and evidence records are UTF-8 JSON objects beneath one project root.
Referenced FPGA packages, artifacts and release reports may be binary or text
regular files. Use only documented JSON keys; the offline tools reject missing
and extra keys. Use real UTC formatted `YYYY-MM-DDTHH:MM:SSZ`, finite JSON
numbers, Vpeak for voltage, non-symlink regular files and project-relative
paths. Never include credentials.

## 1. Qualification request

Top-level exact keys:

```text
request_schema_version       1
qualification_id             unique safe identifier
created_at_utc               current UTC
mode                         active | read-only
purpose                      operator intent
probe_max_age_seconds        1..86400
topology
driver_expectation
acquisition
safety
one_shot                     object for active; null for read-only
acceptance
run_record_path              new project-relative path
```

`topology` exact keys:

```text
output_channel               OUT1
input_channel                IN1
connection                   inspected cable/path description
physically_verified          true
shared_reference_verified    true
```

`driver_expectation` exact keys:

```text
classification               pdg1 | approved-advanced | unknown-read-only
expected_magic_ascii         string or null
expected_abi_major           integer or null
minimum_abi_minor            integer or null
expected_driver_version      string or null
approved_contract_sha256     64 lowercase hex or null
required_capabilities        array
contract_review_path         project-relative path or null
```

Active mode requires at least `event-counter`, `safe-stop`, `single-writer` and
`watchdog`. `unknown-read-only` requires every expectation except
classification/capabilities to be null and capabilities to be empty.

`acquisition` exact keys:

```text
sample_rate_hz
record_samples               integer >= 16
pretrigger_samples           integer, 0 <= value < record_samples
trigger_mode
sample_units
baseline_duration_seconds    positive
minimum_baseline_records     positive integer
```

`safety` exact keys:

```text
maximum_output_volts_peak       positive
input_headroom_fraction         >0 and <=1
maximum_output_window_seconds   >0 and <=5
writer_id                       unique for this run
watchdog_timeout_seconds        >0 and <=5
emergency_stop_action
emergency_stop_verification
restore_action
restore_verification
```

`one_shot` exact keys:

```text
amplitude_volts_peak              positive and <= safety ceiling
pulse_width_seconds               >0 and <=5
capture_window_seconds            >0, <=5 and <= output window
polarity
deterministic_seed                non-negative integer
expected_emitted_candidate_count  exactly 1
```

`acceptance` exact keys:

```text
minimum_detection_fraction        0..1
maximum_detection_fraction        0..1 and >= minimum
maximum_candidate_loss_fraction   0..1
maximum_measured_input_volts_peak positive and within IN1 rating
require_no_overrange              Boolean
```

## 2. Probe and host-key evidence

Use the raw probe, target manifest and normalized report schemas from
`$deploy-red-pitaya-project`. The manifest role is `generator`. The planner
reruns the sibling analyzer and requires byte-semantic equality with the
supplied normalized report, `compatible: true`, and empty warnings/errors.

Use the deploy skill's host-key schema with:

```text
host_key_evidence_schema_version  1
role                              generator
captured_at_utc
host
address
algorithm
ssh_host_key_sha256
verification_method
verified_by
verification_reference
result                            verified
```

The host, address and fingerprint must equal the fresh normalized report.

## 3. Physical topology, calibration and ratings

Exact keys:

```text
evidence_schema_version          1
captured_at_utc
board_machine_id
ssh_host_key_sha256
output_channel                   OUT1
input_channel                    IN1
physically_verified              true
shared_reference_verified        true
physical_output_isolated         true only for read-only unknown driver
connection_description
output_limit_volts_peak          reviewed OUT1 limit
input_range_volts_peak           reviewed IN1 Vpeak range
attenuation_db                   measured, non-negative
attenuation_uncertainty_db       measured, non-negative
coupling
termination_ohms                 positive
calibration_reference
rating_reference
verified_by
```

The planner computes worst-case IN1 Vpeak using:

```text
credible attenuation = max(0, attenuation_db - uncertainty_db)
IN1 Vpeak = commanded OUT1 Vpeak * 10^(-credible attenuation / 20)
```

It rejects values above either the requested measured-input ceiling or:

```text
input_range_volts_peak * input_headroom_fraction
```

## 4. Generator contract and evidence

Register contract exact keys:

```text
schema_version       1
namespace
magic_ascii
abi_major
abi_minor
register_base        aligned hexadecimal
address_span         aligned hexadecimal
capabilities
registers
```

Each register has exact keys `name`, `offset`, `access`, `role`. Active mode
requires unique aligned offsets inside the span and semantic roles:

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

Driver evidence exact keys:

```text
driver_evidence_schema_version   1
captured_at_utc
source
board_machine_id
ssh_host_key_sha256
read_only_probe                  true
output_enabled_readback          false for active
writer_owner                     null for active
contract
```

The nested `contract` has exact keys `magic_ascii`, `abi_major`, `abi_minor`,
`register_contract_sha256`, `register_base`, `address_span`, `capabilities`,
and `driver_version`.

The independent contract review uses the same schema as the two-board
qualifier: schema version, classification, exact contract hash/magic/ABI,
driver version, sorted approved capabilities, independence Boolean, review
UTC, reviewer identity and unique review reference.

Magic, ABI, driver version and register-contract hash prove interface
compatibility. They are not a unique identity for the loaded FPGA build.

## 5. Complete generator-writer inventory

Active mode requires a fresh read-only inventory with exact keys:

```text
writer_inventory_schema_version      1
captured_at_utc
board_machine_id
ssh_host_key_sha256
read_only_inventory                  true
categories
cooperative_lock_id
cooperative_lock_scope
cooperative_lock_bypass_reviewed     true
all_processes_reviewed               true
bypass_capable_writers               []
result                               exclusive
reviewed_by
review_reference
```

`categories` must contain exactly:

```text
legacy_web_apis
lua_endpoints
root_helpers
services
devmem_tools
```

Each category has exact keys `inspection_method` and `observations`. Each
observation has:

```text
identifier
write_capable                        Boolean
state
control                              none | disabled | stopped |
                                     plan-lock-participant | bypass
owner                                identifier or null
evidence                             concrete observation/reference
```

A non-writer uses `control: none`. A write-capable entry must be disabled,
stopped or the plan-lock participant. `bypass` always rejects the plan.
Across all five categories there must be exactly one
`plan-lock-participant`; its identifier must equal `request.safety.writer_id`.
An empty process list, a free cooperative lock or a single service check does
not prove exclusivity. The evidence must review bypass paths including direct
register and `/dev/mem` access.

## 6. Loaded FPGA identity and release evidence

Active mode requires a read-only identity snapshot with exact keys:

```text
fpga_identity_evidence_schema_version  1
captured_at_utc
board_machine_id
ssh_host_key_sha256
read_only_probe                        true
loaded_build_id                        32..64 lowercase hexadecimal
build_identity_source                  hardware/read-only source description
qualified_package_path                 project-relative regular file
qualified_package_sha256
artifact_path                          project-relative loaded artifact
loaded_artifact_sha256
release_receipt_path                   project-relative JSON
qualified_release_receipt_sha256
release_review_path                    project-relative JSON
register_contract_sha256
```

The loaded build ID is a unique release identity exposed by the loaded FPGA or
an equally authoritative read-only hardware identity register. It must not be
synthesized from ABI magic, a driver version or a contract hash. The planner
hashes the named package, artifact, receipt and review itself.

Release receipt exact keys:

```text
release_receipt_schema_version  1
result                          qualified
build_id
package_sha256
artifact_sha256
register_contract_sha256
created_at_utc
issuer_identity
receipt_reference
```

Independent release review exact keys:

```text
fpga_release_review_schema_version  1
result                              approved
build_id
package_sha256
artifact_sha256
release_receipt_sha256
register_contract_sha256
timing
constraints
drc
independent_from_builder            true
reviewed_at_utc
reviewer_identity
review_reference
```

Each of `timing`, `constraints` and `drc` has exact keys:

```text
status          passed
report_path     project-relative regular file
report_sha256
summary
```

The receipt must precede the independent review, and the review must precede
the request. Every hash and build ID must agree through identity, package,
artifact, receipt, review, reports and register contract. The only accepted
release status is exactly `passed`; `blocked`, `failed`, `incomplete`, missing
or any other value forbids active output.

For `unknown-read-only`, omit both writer-inventory and FPGA-identity command
inputs. The planner records null active-release fields and retains the
physically isolated, no-write diagnostic path.

## 7. IN1 pre-state

Exact keys:

```text
state_schema_version          1
captured_at_utc
capture_method
board_machine_id
ssh_host_key_sha256
input_state
restore_verification_method
```

`input_state` exact keys:

```text
state_format_version          1
channel                       IN1
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

The final measured restore record must contain the same complete
`input_state`; the reporter compares the objects exactly.

## 8. Independent watchdog evidence

Exact keys:

```text
watchdog_evidence_schema_version  1
captured_at_utc
board_machine_id
ssh_host_key_sha256
watchdog_type                     hardware-backed | independent-supervisor
independent_from_writer           true
timeout_seconds                   positive, <= requested timeout and <=5
cutoff_method
verification_result              passed
output_disabled_after_cutoff      true
verification_reference
verified_by
```

A timer serviced only by the same unbounded writer process is not independent.

## 9. Deployment-derived authorization

Authorization bundle exact keys:

```text
authorization_bundle_schema_version  1
source_skill                         deploy-red-pitaya-project
deployment_plan                      project-relative path
qualification_binding                project-relative path
```

The deployment plan must deeply validate with commit gates and have
`stage_authorized`, `precommit_live_actions_authorized` and
`functional_output_test_authorized` true. It must bind the same board and host
key, target role `generator`, allow a live output test and enforce an equal or
lower Vpeak ceiling.

Qualification binding exact keys:

```text
qualification_authorization_schema_version  1
source_skill                                deploy-red-pitaya-project
result                                      authorized
qualification_id
request_sha256
deployment_id
deployment_plan_core_sha256
functional_output_receipt_sha256
maximum_output_volts_peak
voltage_unit                                Vpeak
single_use_nonce
recorded_at_utc
expires_at_utc                              no more than one hour later
approver_identity
authorization_reference
```

Use a new nonce and authorization reference for every plan.

## 10. Run record

Exclusive-create the immutable plan's `run_record_path` before live actions.
Exact keys:

```text
run_record_schema_version  1
qualification_id
plan_sha256
single_use_nonce
started_at_utc
executor_id
writer_id
creation_method            exclusive-create
```

Never delete or overwrite a run record to reuse authorization.

## 11. Observations and restore readback

Observation top-level exact keys:

```text
observation_schema_version       1
qualification_id
plan_sha256
mode
started_at_utc
completed_at_utc
board_machine_id
ssh_host_key_sha256
phases
run_record                       file binding or null
acquisition_restore_readback     file binding or null
final_output_disabled
final_output_verification
zero_dead_time_claimed           false
raw_artifacts                    non-empty file-binding array
notes                            string array
```

Each phase exact keys:

```text
phase_id
started_at_utc
ended_at_utc
result                           pass | fail | skipped
output_enabled_observed
emitted_candidate_count
detected_candidate_count
measured_peak_volts
overrange
watchdog_cutoff_ready
writer_owner
loaded_fpga_build_id_observed
writer_inventory_result_observed
measurement_provenance           measured
```

In active mode, phase `verify-loaded-fpga-and-writers` must record the exact
plan-bound build ID and `writer_inventory_result_observed: exclusive`.
Both values are null in all other phases. Any build-ID drift or non-exclusive
writer result is a structural rejection before the one-shot, not an
acceptance-threshold failure.

Restore readback exact keys:

```text
restore_evidence_schema_version  1
qualification_id
board_machine_id
ssh_host_key_sha256
captured_at_utc
input_state
comparison_method
```

File bindings everywhere use exact keys `path`, `sha256`, `size_bytes`.
