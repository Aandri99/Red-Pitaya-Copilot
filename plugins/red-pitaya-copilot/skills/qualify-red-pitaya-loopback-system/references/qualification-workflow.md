# Two-board qualification workflow

Use this procedure for a generator whose one output is physically fanned out to
one input on the generator board and one input on a separate acquisition board.
Treat all names, addresses, channels, ranges and voltage values as untrusted
until current evidence establishes them.

## Contents

1. Roles and authority
2. Workspace layout
3. Physical declaration
4. Exact board binding
5. Driver gate
6. Acquisition pre-state
7. Authorization handoff
8. Immutable plan
9. Active execution
10. Read-only fallback
11. Failure and restoration

## 1. Roles and authority

Maintain these roles:

- **generator**: owns the only output and a local digitized loopback input;
- **acquisition**: digitizes the remote branch and never writes generator state;
- **writer**: the only process allowed to write generator control;
- **reader-local** and **reader-remote**: acquire data without changing output;
- **operator**: confirms physical wiring, limits and emergency-stop reachability.

Use `$deploy-red-pitaya-project` for all SSH identity, staging, precommit and
physical-output authorization. This skill consumes those receipts; it does not
authorize itself.

Do not run generator writes and FPGA loads in parallel. Bring the acquisition
consumer to a known state before enabling the generator.

## 2. Workspace layout

Use one dedicated local project root:

```text
qualification-workspace/
  inputs/
  contracts/
  evidence/
  plans/
  raw/
  results/
```

Keep every referenced file below this root. The tools reject escape paths and
symlinks. Store no passwords, private keys, Arduino Cloud secrets or tokens.

Use a new qualification ID and new filenames for each attempt. Never overwrite
an earlier plan or report.

## 3. Physical declaration

Before network access, have an operator inspect and record:

1. exact generator output channel;
2. exact generator local input channel;
3. exact acquisition remote input channel;
4. fanout/splitter/coupler type;
5. shared signal-reference/ground arrangement;
6. local path attenuation and uncertainty plus a project-local calibration JSON;
7. remote path attenuation and uncertainty plus a project-local calibration JSON;
8. both input Vpeak ranges, coupling and termination plus rating JSON files;
9. a positive maximum generator Vpeak supported by the source and every
   downstream component, recorded in an output-limit rating JSON that also
   declares the deployment ceiling's unit as Vpeak;
10. emergency-stop action that remains reachable if the app, network or writer
    hangs.

Do not infer attenuation from a connector count. Measure it or cite a current
calibration/device record. Include uncertainty. Calculate the highest possible
input using the *least* attenuation within uncertainty:

```text
worst attenuation dB = max(0, nominal attenuation - uncertainty)
input Vpeak = output Vpeak × 10^(-worst attenuation dB / 20)
```

Require the predicted input to remain below:

```text
input range Vpeak × declared headroom fraction
```

The plan generator repeats this calculation for both paths.

## 4. Exact board binding

Follow the deployment skill's `probe-and-identity.md` separately for both
boards.

For each board:

1. independently verify the current SSH host-key fingerprint;
2. perform the documented read-only raw probe;
3. record a real IP address and DNS/mDNS host label;
4. record machine ID, hostname, serial/MAC evidence, OS and exact profile model;
5. record `/opt/redpitaya` source, type and mount options;
6. record a real UTC capture time and probe source;
7. normalize with `analyze_probe.py`;
8. save a separate independent host-key verification JSON;
9. resolve every warning.

Pass expected role `generator` for one report and `acquisition` for the other.
The qualification planner requires:

- `compatible: true`;
- empty blocking errors and warnings;
- exact role match;
- verified SSH fingerprint;
- expected and observed OS/model equality;
- capture age inside `probe_max_age_seconds`;
- different machine IDs and different SSH keys.

It re-runs the deployment analyzer from each hash-bound raw probe and target
manifest and requires the result to equal the supplied normalized report. In
active mode, the corresponding deployment plan must bind the same manifest
and report hashes.

A hostname resembling the expected device is not sufficient.

Re-probe immediately before execution. Abort and create a new plan if machine
ID, host key, OS, model, role, driver or physical route changes.

## 5. Driver gate

Read driver identity/status without writing:

- output-enable readback;
- current writer owner;
- magic;
- ABI major/minor;
- register-contract SHA-256;
- register base and span;
- capabilities;
- driver version.

Read addresses only from the exact contract file under test. Never try a likely
base address.

Classify:

### PDG1

Permit an active plan only when all are true:

- observed magic is exactly `PDG1`;
- observed contract SHA-256 equals the approved contract file;
- ABI major equals the requested major;
- ABI minor is at least the requested minimum;
- observed driver/service version equals the approved requested version;
- base, span and capabilities equal the contract;
- required active capabilities include event counter, safe stop, single writer
  and watchdog;
- register base, span and offsets are valid aligned hexadecimal values;
- the non-empty register map contains the mandatory semantic roles for magic,
  ABI, control, status, watchdog and both event-counter words;
- an independent contract-review record binds the exact hash, ABI, driver
  version and capabilities;
- output is observed disabled.

### Approved advanced

Apply all exact checks above and require a separate review JSON. The review must
bind:

- classification `approved-advanced`;
- exact contract SHA-256;
- exact magic and ABI major;
- real reviewer identity, review reference and UTC time.

Do not treat the application's label "advanced" as approval.

### Unknown

Set request classification `unknown-read-only` and mode `read-only`. Use no
authorization bundle, one-shot or modes. Do not write a stop command, acquire a
writer lease or reset counters through an unknown ABI. Observe only. If
physical output is not already known disabled, require operator isolation
before reading the signal path.

## 6. Acquisition pre-state

Before changing either acquisition path, capture:

- both machine IDs;
- UTC time and read-only capture method;
- generator-input state/settings;
- acquisition-input state/settings;
- exact verification method used after restoration.

Save the complete versioned JSON using the exact endpoint state schema. The
plan binds its SHA-256. After qualification, read back both states and save a
separate measured restore artifact. The reporter compares every state field to
the pre-state; a Boolean claim or copied pre-state hash is not enough.

Do not call a default configuration a restore.

## 7. Authorization handoff

For an active run, use `$deploy-red-pitaya-project` to create and advance one
deployment plan per board.

Require the following distinct, deployment-bound authorization receipts:

| Board | Required gates |
|---|---|
| Generator | `stage_authorized`, `precommit_live_actions_authorized`, `functional_output_test_authorized` |
| Acquisition | `stage_authorized`, `precommit_live_actions_authorized` |

Each receipt must bind deployment ID, immutable deployment plan core, machine
ID, SSH key, scope, approver identity/reference and time. Authorization
references must be unique.

First deep-validate both final plans through the sibling deployment validator
with the common project root. The qualification planner repeats that deep
validation; a minimal or receipt-shaped substitute is rejected.

Create a qualification authorization bundle containing the two
project-relative deployment-plan paths, a project-relative qualification
binding and `source_skill: deploy-red-pitaya-project`. The binding scopes the
generator output receipt to the exact qualification ID, request SHA-256, both
deployment cores, generator receipt SHA-256, explicit Vpeak ceiling, unique
nonce and an expiry no more than one hour after recording.

The generator deployment contract must:

- allow a live output test;
- declare a positive maximum output voltage;
- have a ceiling at least as strict as the qualification request.

Do not interpret "full test", "run it" or a prior session's receipt as a new
authorization for a changed plan. Immediately before the active run,
exclusive-create the plan-SHA/nonce-bound run record. If that file already
exists, treat the authorization as consumed and stop.

## 8. Immutable plan

Populate the request contract from current evidence. Do not use examples as
values.

Generate and deep-validate the plan with the commands in `SKILL.md`. Review:

- exact board identities;
- exact signal paths and worst-case input calculations;
- driver access decision;
- receipt bindings;
- qualification authorization window, Vpeak unit and single-use nonce;
- single-writer ID;
- emergency stop and its independent verification;
- watchdog and maximum runtime;
- baseline duration;
- one-shot ceiling;
- ordered modes;
- required metrics and limits;
- acquisition restore action.

The plan SHA-256 covers every field. Deep validation also re-hashes every input
and reconstructs the plan. Any change requires a new plan.

## 9. Active execution

Use a controller with a `finally`/guaranteed-cleanup path. Never let exception
handling skip safe stop.

### Phase A: bind and safe baseline

1. Re-probe both boards and compare immutable identities.
2. Deep-validate both deployment plans and verify every receipt still matches.
3. Confirm the current time is inside the qualification authorization window.
4. Exclusive-create the run record containing qualification ID, final plan
   SHA-256, single-use nonce, exact start time, executor and writer.
5. Verify emergency stop from the controlling station.
6. Ensure no other writer exists.
7. Invoke reviewed safe stop.
8. Verify output disabled through driver readback and an independent quiet-input
   observation.
9. Capture the declared minimum baseline records on local and remote inputs.
10. Record noise, offsets, saturation/over-range, sample cadence and data gaps.

Do not subtract a baseline captured after output was enabled.

### Phase B: watchdog and one-shot

1. Arm the declared watchdog.
2. Acquire the exact single-writer lease.
3. Confirm emergency stop remains reachable.
4. Configure the immutable deterministic seed and low-amplitude one-shot.
5. Recheck commanded amplitude against the plan before the write.
6. Emit exactly one event.
7. Acquire both inputs for the declared window.
8. Snapshot the authoritative emitted counter and both detector counts.
9. Confirm no over-range, unexpected polarity, FIFO overflow, sequence gap or
   excessive data loss.

Stop here if any check fails. Do not "try a stronger pulse" to diagnose it.

### Phase C: ordered modes

For each immutable mode:

1. confirm phase ID and exact settings;
2. confirm writer lease, watchdog and emergency stop;
3. verify current input ranges/settings;
4. run only the declared repetition count and duration;
5. collect local and remote raw samples concurrently;
6. snapshot emitted/detected/drop/FIFO/epoch counters coherently;
7. stop output between modes;
8. evaluate interim safety and integrity limits.

Never let the UI preview stand in for acquired samples or hardware counters.

### Phase D: stop, counters and restore

1. Invoke safe stop.
2. Verify driver output-disabled readback.
3. Verify both measured inputs are quiet.
4. Release writer lease.
5. Test reset semantics only with output disabled and according to the contract.
6. Test rollover using the reviewed non-destructive/boundary method; do not wait
   for an impractical live wrap.
7. Record counter width, values, epochs and method.
8. Restore both acquisition paths from the hash-bound pre-state.
9. Read back and verify restoration.
10. Record final identity, watchdog, output, FIFO and service state.

## 10. Read-only fallback

For `unknown-read-only`:

- bind both boards;
- observe driver identity and output-disabled status only;
- acquire baseline only if the physical path is already isolated/safe;
- do not invoke driver stop, writer, watchdog, output, counter reset or restore
  writes;
- label the result diagnostic, not active qualification.

The offline plan deliberately has no output-allowed phase.

## 11. Failure and restoration

At the first live failure:

1. invoke the pre-reviewed emergency stop if the ABI is approved;
2. independently verify output disabled;
3. stop the sequence;
4. preserve current raw files and counter snapshots;
5. record the first exact failing action and time;
6. restore acquisition if safe;
7. report whether output and restoration are confirmed, failed or unknown.

If software stop cannot be verified, request physical isolation or power
removal. Do not keep retrying network commands.

Use the deployment skill's failure-state handoff if services, FPGA state,
staged files, backups or mount state may have changed.
