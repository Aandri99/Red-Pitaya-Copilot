---
name: qualify-red-pitaya-loopback-system
description: Safely plan, execute and document a two-board Red Pitaya generator/acquisition loopback qualification with a local generator-input loopback and a remote acquisition input. Use for bounded physical-output tests, generator-to-acquisition correlation, emitted-versus-detected event accounting, PD generator validation, acquisition data-loss/dead-time checks, driver contract gating, counter reset or rollover tests, or evidence-grade JSON/CSV/report generation. Also use when a generator driver is unknown and must remain read-only.
---

# Qualify Red Pitaya Loopback System

Treat qualification as an evidence-bound experiment, not an interactive demo. Bind two exact boards, prove the signal path and limits, delegate every live authorization to `$deploy-red-pitaya-project`, run a fixed low-to-higher-risk sequence under one writer and a watchdog, stop safely, restore acquisition state, then emit immutable evidence.

## Mandatory completion report

Before every final response, read and follow
[../../references/completion-report.md](../../references/completion-report.md).
Apply it even for read-only, diagnostic, partial or blocked work.

The bundled Python tools are offline only and require no third-party package.
They reuse the sibling deployment skill's local analyzers/validators, which may
run bundled local artifact validators. They never make network/SSH calls,
contact a board, grant authorization or operate hardware.

## Define paths

Set these terms before following commands:

- `<plugin-root>`: root of the installed Red Pitaya Copilot plugin.
- `<skill-dir>`: `<plugin-root>/skills/qualify-red-pitaya-loopback-system`.
- `<project-root>`: dedicated local qualification workspace containing every input, receipt, raw artifact and output.

Run bundled scripts from `<skill-dir>/scripts` or use their full paths.

## Read required references

Read each reference before the named work:

- Prepare inputs and conduct any live run: [references/qualification-workflow.md](references/qualification-workflow.md).
- Create request, probe, driver, pre-state, authorization or observation JSON: [references/input-contracts.md](references/input-contracts.md).
- Acquire, label, calculate or interpret measurements and reports: [references/measurement-and-reporting.md](references/measurement-and-reporting.md).

Also read these sibling-skill references before touching boards:

- `<plugin-root>/skills/deploy-red-pitaya-project/SKILL.md`
- `<plugin-root>/skills/deploy-red-pitaya-project/references/probe-and-identity.md`
- `<plugin-root>/skills/deploy-red-pitaya-project/references/gate-evidence-and-transitions.md`
- `<plugin-root>/skills/deploy-red-pitaya-project/references/multi-board-coordination.md`
- `<plugin-root>/skills/deploy-red-pitaya-project/references/acceptance-and-rollback.md`

## Non-negotiable rules

1. Never identify a board from `rp-*.local`, an IP address, cable position or memory. Bind role to a fresh raw probe, a freshly reproduced normalized report, machine ID and a separate independently verified SSH host-key record.
2. Never copy hostnames, channel names, input ranges, termination, attenuation, calibration, register addresses, sample rates, thresholds, voltage limits or timing from this skill. Derive them from current read-only evidence and an operator-inspected cabling declaration.
3. Require a positive `maximum_output_volts_peak`. Reject a null, zero, negative or deployment-contract-exceeding ceiling.
4. Declare both fanout paths: generator output to generator local input, and the same output to the acquisition-board input. Hash-bind a semantic calibration/rating JSON for attenuation plus uncertainty, each input's Vpeak range/coupling/termination and the generator Vpeak limit.
5. Keep one process/lease as the only writer to generator control. Readers may run concurrently. Stop if another writer owns the driver.
6. Make emergency stop available during every phase. Never hide it behind a test loop, mode change or network reconnection.
7. Require output disabled and independently verified before baseline, after every failure and at completion.
8. Capture baseline on both inputs before the first physical-output command.
9. Arm a hardware-backed or independently supervised watchdog before acquiring the writer lease or enabling output.
10. Use an exact, structurally valid driver contract with hexadecimal aligned address bounds, a non-empty register map and all mandatory semantic register roles. Permit writes only for:
    - `pdg1`: observed `PDG1`, compatible ABI, exact contract SHA-256 and a separate independent release review;
    - `approved-advanced`: exact contract plus a separate hash-bound review record;
    - never `unknown-read-only`: collect read-only status only and command no output.
11. Do not guess register offsets after a magic, ABI, address-span or contract-hash mismatch.
12. Obtain `stage_authorized`, `precommit_live_actions_authorized` and `functional_output_test_authorized` through `$deploy-red-pitaya-project`. Deep-run that sibling skill's validator against both real plans. A receipt-shaped substitute is invalid.
13. Bind the deployment receipts to this exact request with a fresh, expiring qualification authorization, exact Vpeak ceiling and unique nonce. Before live work, exclusively create the one-time run record that binds the final plan SHA-256 and nonce.
14. Run one deterministic low-amplitude one-shot before higher-amplitude modes. Never skip directly to a preset.
15. Bound maximum runtime and commanded Vpeak in the immutable plan. Stop immediately on identity drift, watchdog loss, writer conflict, input over-range, FIFO overflow, excessive loss or loss of emergency-stop reachability.
16. Label a generator UI/setting preview `reconstructed`. Label digitized local and remote waveforms `measured`. Label calculations from measured waveforms `derived`.
17. Record emitted and detected counts, delay, gain, correlation, polarity, width, energy, ringing, phase, dead time, data loss, drops, reset behavior and rollover behavior.
18. Preserve raw artifacts. Do not alter a plan, observation file or receipt to make a failed test pass.
19. Restore the exact versioned acquisition pre-state and require a hash-bound post-restore readback that compares every field exactly.
20. Refuse overwrites and symlinks. Use a new qualification ID, nonce and output paths for every run.
21. Never claim the offline tools performed a live test. They only validate local evidence.

## Required workflow

### 1. Freeze live work until inputs exist

Do not connect when the user defers live work. Prepare local contracts and stop.

For an active run, collect:

- raw probes, target manifests, freshly reproduced normalized reports and independent host-key records for both exact roles;
- operator-inspected topology plus hash-bound path calibration, input-rating and output-limit JSON;
- read-only generator driver evidence, exact register-contract file and independent contract review;
- exact acquisition pre-state for both inputs;
- two deeply valid deployment plans, their deployment-skill receipts and the request-bound expiring qualification authorization;
- qualification request with deterministic sequence and acceptance criteria.

Use [references/qualification-workflow.md](references/qualification-workflow.md) for collection order.

### 2. Generate the immutable plan offline

For active qualification:

```text
python generate_qualification_plan.py \
  --project-root <project-root> \
  --request <project-root>/inputs/qualification-request.json \
  --generator-probe-report <project-root>/evidence/generator-probe-report.json \
  --acquisition-probe-report <project-root>/evidence/acquisition-probe-report.json \
  --generator-raw-probe <project-root>/raw/generator-probe.json \
  --acquisition-raw-probe <project-root>/raw/acquisition-probe.json \
  --generator-target-manifest <project-root>/contracts/generator-project.json \
  --acquisition-target-manifest <project-root>/contracts/acquisition-project.json \
  --generator-host-key-evidence <project-root>/evidence/generator-host-key.json \
  --acquisition-host-key-evidence <project-root>/evidence/acquisition-host-key.json \
  --generator-driver-evidence <project-root>/evidence/generator-driver-evidence.json \
  --generator-driver-contract <project-root>/contracts/generator-driver-contract.json \
  --acquisition-pre-state <project-root>/evidence/acquisition-pre-state.json \
  --authorization-bundle <project-root>/evidence/deployment-authorizations.json \
  --output <project-root>/plans/qualification-plan.json
```

For `unknown-read-only`, omit `--authorization-bundle`; the request must contain no one-shot or modes.

The generator rejects:

- stale, warning-bearing, incompatible or role-mismatched reports, reports not exactly reproducible from bound raw probes/manifests, or mismatched host-key evidence;
- the same board or SSH identity in both roles;
- unknown active drivers;
- PDG1 magic, ABI, aligned address/span, required register-role, capability, review, version or contract-hash mismatch;
- an unreviewed advanced contract;
- output above the deployment safety ceiling or either input headroom limit;
- deployment plans that fail the sibling deep validator, fabricated receipts, missing request-bound authorization, stale authorization or nonce reuse;
- output windows above maximum runtime;
- paths outside `<project-root>`, symlinks and existing output files.

### 3. Deep-validate immediately before use

```text
python validate_qualification_plan.py \
  <project-root>/plans/qualification-plan.json \
  --project-root <project-root>
```

Validation recalculates the plan hash, re-hashes every input and reconstructs the entire plan. Any input change invalidates the plan. Generate a new plan; never patch the old one.

### 4. Execute exactly the plan

Use the live procedure in [references/qualification-workflow.md](references/qualification-workflow.md). Never improvise:

1. Re-probe and abort on drift.
2. Revalidate authorization freshness; exclusively create the plan/nonce-bound run record.
3. Confirm emergency stop and single writer.
4. Safe-stop and independently verify disabled output.
5. Capture local and remote baseline.
6. Arm watchdog.
7. Run the deterministic low-amplitude one-shot.
8. Review input ranges, loss and counters.
9. Run modes one at a time.
10. Safe-stop and verify.
11. Check counters with output disabled.
12. Restore and verify acquisition pre-state field-for-field.
13. Save observations and raw artifacts.

The physical-output command is allowed only inside plan phases with `output_allowed: true`.

### 5. Build the evidence bundle offline

Create the exact observation JSON described in [references/input-contracts.md](references/input-contracts.md). Do not invent absent readings or mark skipped phases passed.

```text
python build_qualification_report.py \
  --project-root <project-root> \
  --plan <project-root>/plans/qualification-plan.json \
  --observations <project-root>/evidence/observations.json \
  --generated-at-utc <real-UTC-YYYY-MM-DDTHH:MM:SSZ> \
  --evidence-json <project-root>/results/qualification-evidence.json \
  --measurements-csv <project-root>/results/qualification-measurements.csv \
  --report-md <project-root>/results/qualification-report.md
```

The tool deep-validates the plan, board/driver/authorization bindings, phase order, baseline-before-output timing, waveform provenance, metric completeness, counter behavior, bounded runtime, Vpeak ceiling, safe stop and acquisition restoration. It can emit a valid `FAIL` report; that is evidence, not a tool error.

## Failure behavior

On any live failure:

1. Invoke the pre-reviewed emergency stop.
2. Independently verify output disabled.
3. Do not continue to the next mode.
4. Preserve partial measured data and exact error text.
5. Restore acquisition only if doing so is safe and authorized.
6. Emit a failed evidence report when observations remain structurally trustworthy.
7. Use `$deploy-red-pitaya-project` failure-state handoff for uncertain service, FPGA, mount or backup state.

If output-disable cannot be verified, require physical isolation/power removal by the operator and do not claim safe state.

## Test bundled tooling

Run:

```text
python test_qualification_tools.py
```

The tests create only local transient fixtures. They cover valid active/read-only plans, real sibling deployment-plan validation, fabricated plans, raw-probe and host-key substitution, board identity substitution, bound calibration and Vpeak-unit mismatch, voltage/headroom overflow, mandatory driver registers/reviews, stale or replay-shaped authorization, semantic restore mismatch, provenance labels, counter width, overwrite and symlink refusal, and PASS/FAIL report creation. They do not use a network connection.
