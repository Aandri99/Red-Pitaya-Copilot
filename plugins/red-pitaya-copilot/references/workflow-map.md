# Four-skill workflow map

This plugin deliberately separates application design, FPGA design, live
deployment and two-board qualification. They share validated contracts but
have different permissions, evidence and completion criteria.

## Choose the first skill

| User intent | Start with |
|---|---|
| Create a dashboard, HTTP API, WebSocket app, Lua route, native controller or board-side service | `$build-red-pitaya-app` |
| Change RTL, AXI registers, streaming, event capture, buffering, clocks, constraints, device tree or bitstream packaging | `$build-red-pitaya-fpga` |
| Inspect a target, install files, load an FPGA, change a service, remount storage, verify health or roll back | `$deploy-red-pitaya-project` |
| Correlate a generator output with local and remote inputs, quantify event loss/dead time, validate a driver, or create evidence-grade loopback reports | `$qualify-red-pitaya-loopback-system` |

For a complete new instrument, use the skills in this order:

```text
shared manifest
      |
      +--> application architecture and safe API
      |
      +--> FPGA/register implementation when required
      |
      +--> offline package and evidence validation
      |
      +--> target probe, deployment plan, staged install and rollback verification
      |
      `--> two-board binding, single-use run and immutable qualification report
```

Application and FPGA work can proceed in parallel only after `project`, `target`,
`safety` and interface ownership are stable in the manifest.
Qualification begins only after the generator and acquisition deployments have
separate, deeply valid plans and receipt-backed authorization.

## Handoff packet

Every skill hands the next skill:

1. the same validated `redpitaya-project.json` for each target role (a
   two-board qualification has separate generator and acquisition manifests);
2. paths to produced source and artifacts;
3. exact OS release and target-model evidence;
4. validation results;
5. unresolved warnings and unknowns;
6. the safe stop/disable contract;
7. a statement of actions that were not performed.

Do not hand off a conclusion such as "compatible." Hand off the evidence that
allows the next skill to check compatibility independently.

## Permission boundaries

### Application skill may

- create and edit local application source;
- create browser, API and service tests;
- package local application files;
- describe target build commands.

It must not install on a board or load an FPGA.

### FPGA skill may

- create and edit local RTL, constraints, register contracts and device trees;
- run local simulation, lint, synthesis or implementation when tools exist;
- package a proven build for a pinned OS profile.

It must not load the artifact on a board or enable physical output.

### Deployment skill may

- perform read-only board discovery;
- create an offline deployment plan;
- after authorization, stage and install only the resolved project target;
- verify health and execute the prepared rollback.

It must not redesign the app or FPGA while the target filesystem is writable.

### Qualification skill may

- bind two independently identified boards and their reviewed deployment plans;
- validate an operator-declared, calibrated two-path fanout;
- create an immutable, bounded, low-to-higher-risk measurement plan;
- consume a fresh single-use output authorization;
- build evidence-grade JSON, CSV and Markdown reports from measured artifacts.

It must not authorize deployment, invent calibration, bypass a driver gate,
write through an unknown contract, or label reconstructed previews as measured.

## Stop conditions

Stop the dependent operation when any of these is true:

- target role, host key, board model or OS release is not proven;
- `profiles -f`, `monitor -n`/`monitor -i`, the physical identity and the
  matching FPGA directory cannot be reconciled;
- manifest validation fails;
- a native binary was built for another ABI;
- FPGA model, constraints, device tree and register map do not form one build;
- timing fails or unconstrained paths remain;
- the stop/disable action is missing or not idempotent;
- the previous package cannot be backed up;
- `/opt/redpitaya` cannot be restored to its original mount mode;
- staged hashes differ from local hashes;
- health verification is missing;
- a physical output test would be required but is not authorized.
- the two qualification roles resolve to the same machine or SSH identity;
- a fanout path, input rating, attenuation uncertainty or Vpeak limit is unbound;
- the driver lacks an exact reviewed contract, single writer or watchdog;
- a qualification nonce/run record is missing, expired or already consumed;
- acquisition pre-state cannot be restored and verified field-for-field.

Do not bypass a stop condition by changing the manifest to match an observation
unless the user confirms that the observed board is the intended target.

## Example complete requests

### Application only

```text
Use $build-red-pitaya-app to create an OS 3.00-57 Nginx/Lua status dashboard for
model z20_125. It must default safe, expose status and stop routes, and include
local validation. Do not deploy it.
```

### FPGA only

```text
Use $build-red-pitaya-fpga to add a continuous event FIFO to this pinned
z20_125 project. Define and generate the register contract, simulate overflow
behavior, verify CDC/timing, and package for OS 3.00-57. Do not load hardware.
```

### Safe deployment

```text
Use $deploy-red-pitaya-project to create a dry-run plan for this validated
project and recorded probe. Show all gates and rollback steps. Do not connect to
or modify a board.
```

### Full system

```text
Build a Red Pitaya instrument as one project. Start with the application skill,
then use the FPGA skill for deterministic acquisition, and finally use the
deployment skill for separate generator and acquisition dry-run plans. After
both plans are independently authorized, use the qualification skill for a
single-use two-board loopback plan and immutable report. Stop before any live
board write unless all live gates are explicitly satisfied.
```
