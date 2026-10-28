# Single-board OUT1-to-IN1 workflow

Use this sequence without reordering. The offline scripts create and validate
artifacts; they do not operate a board.

## 1. Create a dedicated workspace

Create new `inputs`, `contracts`, `raw`, `evidence`, `plans` and `results`
directories beneath one project root. Do not reuse another qualification ID,
nonce, run record or output path. Keep secrets outside the workspace.

## 2. Freeze topology and limits

With output disabled:

1. visually identify the exact Red Pitaya;
2. inspect that this board's `OUT1` is connected to this board's `IN1`;
3. verify reference/ground and termination;
4. record cable, attenuation, uncertainty and calibration reference;
5. record OUT1 Vpeak limit and IN1 range/coupling/termination;
6. calculate worst-case IN1 Vpeak before choosing amplitude.

For an unknown driver, physically isolate output and use read-only mode.

## 3. Bind the exact board

Follow the deploy skill's read-only probe procedure:

1. verify the SSH host key independently;
2. capture host/address/fingerprint, hostname, machine ID, serial/MAC, OS,
   model/profile and `/opt/redpitaya` mount evidence;
3. save raw probe and generator-role target manifest;
4. normalize with the deploy analyzer;
5. resolve every warning;
6. save separate host-key verification evidence.

Re-probe immediately before execution. Stop on any identity drift.

## 4. Discover the generator without writes

Identify the active generator process, service, register owner and output
readback using read-only methods. Do not probe likely register addresses.

For active mode:

1. obtain the exact register contract and its SHA-256;
2. verify magic, ABI, driver version, base/span and capabilities;
3. confirm semantic register roles and aligned offsets;
4. confirm output disabled and writer owner free;
5. obtain independent contract review;
6. verify safe-stop, event counter, single-writer and watchdog capabilities.

Then prove the loaded FPGA release separately:

1. read a unique build ID from an authoritative read-only FPGA identity source;
2. bind that ID to the exact qualified package and loaded FPGA artifact;
3. bind the release receipt to package, artifact and register-contract hashes;
4. obtain an independent release review;
5. bind its timing, constraint-completeness and DRC reports;
6. require all three statuses to be exactly `passed`.

Magic, ABI and driver version cannot substitute for build identity. Stop on a
missing/mismatched ID, tampered binding or any blocked, failed or incomplete
release status.

Inventory every possible generator writer with read-only methods:

1. legacy web applications and APIs;
2. Lua applications/endpoints;
3. privileged/root helper scripts and binaries;
4. services, timers, processes and scheduled jobs;
5. direct register and `/dev/mem` tools.

Record every write-capable item and how it is disabled, stopped or made a
participant in the same plan lock. There must be exactly one plan-lock
participant matching the immutable writer ID and no bypass-capable writer.
A cooperative lock alone cannot prove exclusivity because root helpers,
legacy APIs or direct-memory tools may ignore it.

On observed OS 2.07-48 systems, stock Nginx with the Red Pitaya hardware module
can retain `/dev/mem` even while no visible generator app is running. Record
its PID, executable, descriptor and module/config evidence. Treat it as a
legacy bypass-capable owner for active output qualification unless a reviewed
platform-specific control proves it cannot mutate the planned output path.
This blocks physical writer exclusivity; it does not block read-only IN1,
output-passive deployment or simulation-only acceptance.

If any item is unknown or mismatched, switch to `unknown-read-only`. Do not
guess or write a stop command through an unknown ABI.

## 5. Capture IN1 pre-state

Read and save every versioned IN1 setting before changing acquisition:
enabled state, decimation, trigger source/level/delay, gain, coupling,
termination and units. Bind machine ID and SSH key. State the exact readback
method that will verify restoration.

## 6. Prove independent cutoff

Test the hardware watchdog or independent supervisor at a safe condition.
Record timeout, method, reviewer and measured proof that timeout disables
output. The cutoff must remain effective if the writer hangs.

Do not count a `finally` block in the writer process as independent cutoff.

## 7. Obtain deployment-bound authorization

Use `$deploy-red-pitaya-project` for the same exact board:

1. deeply validate its deployment plan;
2. advance stage and precommit gates with distinct evidence;
3. require the functional physical-output authorization;
4. create the qualification binding to request hash, deployment core, output
   receipt, Vpeak ceiling, one-hour-or-shorter window and fresh nonce.

Do not create an authorization-looking substitute in this skill.

## 8. Generate and review the plan

Run `generate_single_board_plan.py`, then
`validate_single_board_plan.py`. Review the printed plan hash and:

- board identity and host key;
- `OUT1 -> IN1` same-board declaration;
- worst-case IN1 Vpeak and headroom;
- contract hash, ABI, writer and watchdog;
- unique loaded FPGA build ID and its package/artifact/receipt/review chain;
- passing timing, constraints and DRC reports;
- complete five-category writer inventory with no bypass path;
- deployment receipt and authorization expiry;
- one-shot amplitude/window;
- run-record path;
- phase order and restoration contract.

If anything changes, generate a new plan and new authorization.

## 9. Execute active qualification

Use a controller with guaranteed cleanup. Keep emergency stop available
outside its main loop.

### Phase 1: identity and safe state

1. Re-probe machine ID and host key.
2. Deep-validate the plan and authorization time.
3. Exclusive-create the run record using atomic create-if-absent.
4. Confirm emergency-stop reachability.
5. Confirm the inventory still shows no other generator writer.
6. Invoke the reviewed safe stop.
7. Verify output-disabled readback.
8. Independently observe quiet IN1.

### Phase 2: baseline

1. Apply only the plan's IN1 settings.
2. Capture the required baseline duration/record count.
3. Record offset, RMS/noise, peak, saturation and data gaps.
4. Save raw samples as measured artifacts.

Do not enable output or subtract a post-output baseline.

### Phase 3: verify loaded FPGA and every writer path

1. Re-read the unique loaded FPGA build ID from the same authoritative source.
2. Compare it exactly with the plan-bound release build ID.
3. Repeat all five writer-inventory categories.
4. Confirm timing, constraints and DRC remain bound to that same release.
5. Record the observed build ID and `exclusive` writer result.
6. Abort before output on any drift, unknown surface or bypass-capable writer.

This phase must occur after baseline and immediately before watchdog/lease and
output. Do not infer success from the earlier planning snapshot.

### Phase 4: watchdog and lease

1. Arm the independent cutoff.
2. Verify cutoff readiness.
3. Acquire the exact plan writer lease.
4. Reconfirm no competing owner and emergency-stop reachability.

### Phase 5: low-amplitude one-shot

1. Check the exact phase, amplitude and five-second maximum again.
2. Snapshot the coherent generator candidate counter before output.
3. Arm IN1 acquisition.
4. Emit exactly one deterministic event.
5. Acquire the bounded record.
6. Snapshot the candidate counter after output.
7. Count detected IN1 candidates using the frozen method.
8. Record measured peak, over-range, writer and watchdog status.

Do not increase amplitude after a failure.

### Phase 6: stop

1. Invoke safe stop in the guaranteed cleanup path.
2. Verify output-disabled readback.
3. Independently observe quiet IN1.
4. Release the writer.

### Phase 7: restore

1. Restore every IN1 field from the bound pre-state.
2. Read every field back into a new restore artifact.
3. Preserve both pre-state and readback.

## 10. Execute read-only fallback

For `unknown-read-only`:

- require physical output isolation;
- re-probe exact identity;
- observe driver evidence and IN1 baseline only;
- omit active FPGA-release, writer-inventory, watchdog and authorization inputs
  from the immutable plan;
- do not issue safe-stop, lease, watchdog, output, reset or acquisition writes;
- do not create a run record or claim active qualification.

## 11. Build and preserve the report

Create observations and run `build_single_board_report.py`. Preserve:

- request and every input;
- immutable plan;
- deployment receipts and authorization;
- loaded FPGA identity, package, artifact, receipt, independent release review
  and timing/constraints/DRC reports;
- complete writer inventory and pre-output identity/inventory re-read;
- run record;
- raw baseline/event samples;
- counter/status snapshots;
- restore readback;
- evidence JSON, criteria CSV and Markdown report.

`FAIL` is evidence. Do not edit measured values or delete a consumed run record.

## 12. Failure response

On any failure, including FPGA build drift, release-status failure or a newly
discovered bypass writer:

1. safe-stop through the approved ABI;
2. verify disabled independently;
3. preserve first error and partial artifacts;
4. release writer only after safe state;
5. restore IN1 when safe;
6. report failed/unknown state honestly.

If output disable cannot be verified, require physical isolation or power
removal. Use the deploy skill's failure-state handoff for uncertain service,
FPGA, mount, staged-file or backup state.
