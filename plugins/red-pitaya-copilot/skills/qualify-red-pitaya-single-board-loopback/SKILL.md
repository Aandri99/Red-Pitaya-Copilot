---
name: qualify-red-pitaya-single-board-loopback
description: Safely plan, execute and document an evidence-bound single-board Red Pitaya OUT1-to-IN1 loopback qualification. Use for bounded generator/acquisition self-tests on one exact board, generator register-contract validation, writer/watchdog gating, low-amplitude one-shot checks, emitted-versus-detected candidate accounting, acquisition restoration, PASS/FAIL evidence generation, or read-only inspection of an unknown generator driver. Do not use this in place of the separate two-board qualifier.
---

# Qualify Red Pitaya Single-Board Loopback

Treat the loopback as a controlled physical-output experiment. Bind one exact
board to one host key and one deployment plan, prove the physical OUT1-to-IN1
path and voltage headroom, capture baseline first, arm an independent cutoff,
emit one low-amplitude event for at most five seconds, restore IN1 exactly and
produce immutable evidence.

## Mandatory completion report

Before every final response, read and follow
[../../references/completion-report.md](../../references/completion-report.md).
Apply it even for read-only, diagnostic, partial or blocked work.

The bundled Python tools are offline only. They do not use SSH, network APIs,
subprocesses or hardware. They import the sibling deployment skill's local
probe and deployment validators, but never grant authorization.

## Choose the correct qualifier

- Use this skill only when `OUT1` and `IN1` belong to the same Red Pitaya.
- Use `$qualify-red-pitaya-loopback-system` when generator and acquisition are
  different boards, even if the generator also has a local loopback.
- Do not collapse two board identities into this schema.

## Define paths

- `<plugin-root>`: installed Red Pitaya Copilot plugin.
- `<skill-dir>`:
  `<plugin-root>/skills/qualify-red-pitaya-single-board-loopback`.
- `<project-root>`: a new dedicated qualification workspace containing every
  input, receipt, raw artifact and output.

Run scripts from `<skill-dir>/scripts` or use their full paths.

## Read required references

Read all three before conducting live work:

- Input JSON and evidence schemas:
  [references/input-contracts.md](references/input-contracts.md).
- Exact collection, authorization, execution, stop and restoration sequence:
  [references/qualification-workflow.md](references/qualification-workflow.md).
- Candidate counting, provenance, acceptance and report interpretation:
  [references/measurement-and-reporting.md](references/measurement-and-reporting.md).

Before any board access also read:

- `<plugin-root>/skills/deploy-red-pitaya-project/SKILL.md`
- its `references/probe-and-identity.md`
- its `references/gate-evidence-and-transitions.md`
- its `references/acceptance-and-rollback.md`

## Non-negotiable rules

1. Bind the board to a fresh raw probe, a freshly reproduced warning-free
   normalized report, machine ID and independently verified SSH host key.
   A hostname, IP address, cable position or previous session is not identity.
2. Require an operator-inspected declaration that this same board's `OUT1` is
   connected to its `IN1`, with shared reference/ground verified.
3. Hash-bind the reviewed output rating, input Vpeak range, coupling,
   termination, path attenuation and attenuation uncertainty. Calculate
   worst-case IN1 Vpeak using the lowest credible attenuation.
4. Never infer register offsets. Bind a non-empty aligned register contract.
   Active mode requires exact magic, ABI, version, contract hash, capabilities
   and semantic roles for control, status, watchdog and coherent event count.
5. Do not treat ABI magic, driver version or a register-contract hash as FPGA
   build identity. Active mode requires a unique read-only loaded build ID
   bound to the exact qualified package, loaded artifact, release receipt and
   independent release review. That review must bind passing timing,
   constraint-completeness and DRC reports. `blocked`, `failed`, `incomplete`
   or missing status forbids output.
6. Do not treat a cooperative lock as proof of single-writer control. Inventory
   legacy web APIs, Lua endpoints, privileged/root helpers, services/processes
   and `/dev/mem` tools. Reject every bypass-capable or uncontrolled writer and
   require exactly one plan-lock participant matching the immutable writer ID.
7. Permit one generator writer only. Require the preflight owner to be free,
   re-read the loaded FPGA build ID and complete writer inventory immediately
   before output, acquire the exact plan writer lease and stop on drift.
8. Require a hardware-backed watchdog or cutoff supervised independently from
   the writer. Verify that timeout disables output before the run.
9. Derive stage, precommit and physical-output authorization through
   `$deploy-red-pitaya-project`. Bind its deeply valid deployment plan, output
   receipt, exact board identity, Vpeak ceiling, expiry and unique nonce.
10. Exclusive-create the plan/nonce-bound run record. If it already exists,
   treat the authorization as consumed; do not reuse or delete it.
11. Verify output disabled, then capture the complete baseline before any
   output command. Baseline captured after output is invalid.
12. Run only the immutable low-amplitude one-shot. Its output/capture window
    must be positive and no longer than five seconds.
13. Count emitted candidates from the approved generator counter delta and
    detected candidates from IN1. Do not call candidate agreement proof of
    continuous or zero-dead-time acquisition.
14. Stop immediately on identity drift, host-key drift, FPGA build-ID drift,
    bypass-writer discovery, release-gate failure, writer conflict,
    watchdog loss, over-range, authorization expiry or emergency-stop loss.
15. In a guaranteed cleanup path, safe-stop, independently verify OUT1
    disabled, release the writer and restore the exact bound IN1 pre-state.
16. Compare the measured restoration readback field-for-field. A Boolean
    "restored" claim, default configuration or copied hash is insufficient.
17. Preserve raw measured artifacts. Label configured previews
    `reconstructed`, ADC/status/counters `measured`, and calculations `derived`.
18. For `unknown-read-only`, physically isolate output, make no driver or
    acquisition writes, omit authorization/watchdog/one-shot and report only
    read-only diagnostics.
19. Refuse symlinks and overwrites. Create a new qualification ID, nonce,
    run-record path and result paths for each run.

## Required workflow

### 1. Collect current evidence

Follow [references/qualification-workflow.md](references/qualification-workflow.md).
Collect:

- raw probe, target manifest, normalized report and independent host-key record;
- physical topology/calibration/rating evidence for `OUT1 -> IN1`;
- read-only generator evidence, register contract and independent review;
- read-only loaded FPGA identity plus qualified package, exact artifact,
  release receipt, independent release review and passing timing,
  constraints and DRC reports;
- read-only writer inventory covering legacy web APIs, Lua endpoints, root
  helpers, services/processes and `/dev/mem` tools;
- complete IN1 pre-state;
- independent watchdog/cutoff evidence;
- deployment plan, receipts and qualification authorization for active mode;
- strict qualification request and unused run-record path.

Do not connect when live work is deferred. Preparing contracts locally is safe;
executing the offline tools is not a live run.

### 2. Generate the immutable plan

For active mode:

```text
python generate_single_board_plan.py \
  --project-root <project-root> \
  --request <project-root>/inputs/request.json \
  --probe-report <project-root>/evidence/probe-report.json \
  --raw-probe <project-root>/raw/probe.json \
  --target-manifest <project-root>/contracts/project.json \
  --host-key-evidence <project-root>/evidence/host-key.json \
  --topology-evidence <project-root>/evidence/topology.json \
  --driver-evidence <project-root>/evidence/driver.json \
  --driver-contract <project-root>/contracts/driver-contract.json \
  --acquisition-pre-state <project-root>/evidence/in1-pre-state.json \
  --writer-inventory-evidence <project-root>/evidence/writer-inventory.json \
  --fpga-identity-evidence <project-root>/evidence/loaded-fpga-identity.json \
  --watchdog-evidence <project-root>/evidence/watchdog.json \
  --authorization-bundle <project-root>/evidence/authorization-bundle.json \
  --output <project-root>/plans/qualification-plan.json
```

For `unknown-read-only`, set request mode/classification accordingly and omit
`--writer-inventory-evidence`, `--fpga-identity-evidence`,
`--watchdog-evidence` and `--authorization-bundle`.

Generation rejects mismatched identity, unreproducible probes, warning-bearing
evidence, topology/rating disagreement, contract drift, unbound/mismatched
loaded FPGA build identity, tampered FPGA package/artifact/receipt, any
non-passing timing/constraints/DRC status, incomplete writer inventory,
bypass/uncontrolled writer, missing watchdog, unsafe Vpeak, invalid deployment
receipts, stale authorization, consumed nonce, symlink or existing output.

### 3. Deep-validate immediately before use

```text
python validate_single_board_plan.py \
  <project-root>/plans/qualification-plan.json \
  --project-root <project-root>
```

Deep validation re-hashes every input, reruns deployment validators and
reconstructs the complete plan. Do not patch a plan after failure; create new
evidence and a new plan.

### 4. Execute exactly the plan

Use the live sequence in the workflow reference:

1. re-probe identity and host key;
2. deep-validate plan and authorization freshness;
3. exclusive-create the run record;
4. confirm emergency stop and no competing writer;
5. safe-stop and independently verify OUT1 disabled;
6. capture IN1 baseline;
7. re-read the exact loaded FPGA build ID and repeat the full writer inventory;
8. abort if the build ID differs or writer inventory is not exclusive;
9. arm the independent cutoff;
10. acquire the plan writer lease;
11. execute the one bounded one-shot;
12. capture measured waveform/status and coherent candidate-counter deltas;
13. safe-stop, verify disabled, release writer;
14. restore IN1 and read every field back;
15. preserve observations and raw artifacts.

Only the `low-amplitude-one-shot` phase permits output.

### 5. Build the evidence bundle

Create observations exactly as documented in
[references/input-contracts.md](references/input-contracts.md), then run:

```text
python build_single_board_report.py \
  --project-root <project-root> \
  --plan <project-root>/plans/qualification-plan.json \
  --observations <project-root>/evidence/observations.json \
  --evidence-json <project-root>/results/evidence.json \
  --measurements-csv <project-root>/results/criteria.csv \
  --report-md <project-root>/results/report.md
```

A valid `FAIL` report is useful evidence. A tool error means the evidence is
structurally inconsistent, tampered, unsafe or unverifiable.

## Failure behavior

At the first live failure:

1. invoke the pre-reviewed safe stop if the ABI is approved;
2. independently verify output disabled;
3. stop the run and preserve the first error plus partial measured data;
4. release the writer only after output disable;
5. restore IN1 only when safe and authorized;
6. emit a `FAIL` report if observations remain structurally trustworthy;
7. use the deploy skill's failure-state handoff if board state is uncertain.

If software disable cannot be verified, require operator physical isolation or
power removal. Never continue by increasing amplitude.

## Test bundled tools

```text
python test_single_board_tools.py
```

The tests are local-only. They cover same-board semantics, identity
substitution, stale/replayed authorization, Vpeak/headroom overflow, output
before baseline, restore mismatch, missing watchdog, driver-contract mismatch,
loaded-build mismatch and pre-output drift, package/artifact/receipt tampering,
blocked/failed/incomplete timing/constraints/DRC, missing writer categories,
bypass writers, invalid plan-lock participants, PASS/FAIL reporting, input
tampering, read-only fallback, overwrite/symlink refusal and absence of
live-network code.
