---
name: build-red-pitaya-fpga
description: Design, extend, review, simulate, build, validate and package FPGA projects for Red Pitaya and STEMlab boards. Use when Codex works with RedPitaya-FPGA, Vivado 2020.1, SystemVerilog or Verilog RTL, AXI register maps, ADC/DAC data paths, FPGA interrupts, event FIFOs, DMA, continuous acquisition, timing closure, CDC/reset analysis, device-tree overlays, red_pitaya.bit, fpga.bit.bin, fpga.bin, OS 2.00-35, OS 2.07-48, OS 3.00-57, or hardware-safe FPGA test plans.
---

# Build Red Pitaya FPGA

Create FPGA deliverables from an explicit board, OS and register contract. Never infer hardware compatibility from a hostname, connector shape, FPGA family, or a successful file copy.

## Mandatory completion report

Before every final response, read and follow
[../../references/completion-report.md](../../references/completion-report.md).
Apply it even for read-only, diagnostic, partial or blocked work.

## Non-negotiable rules

1. Treat a bitstream as board-model-specific and repository-revision-specific.
2. Treat the exact target board or supplied OS image as more authoritative than general documentation.
3. Use Vivado 2020.1 for the supplied Red Pitaya project scripts unless the checked-out repository explicitly proves another version.
4. Keep outputs disabled through reset and reconfiguration. Enable them only in a deliberate hardware test step.
5. Simulate before synthesis; inspect timing, CDC and utilization before packaging.
6. Generate software and RTL register constants from one machine-readable register contract.
7. Package Red Pitaya OS 2.x, including 2.00-35 and 2.07-48, as `fpga.bit.bin`; package OS 3.00-57 as `fpga.bin`. A filename is not proof of a valid binary format.
8. Never load an artifact, write physical registers, remount storage or start a signal output unless the user explicitly authorizes live-board changes.
9. Use `$deploy-red-pitaya-project` for live installation and rollback. This skill may prepare a safe test plan and package, but does not deploy.
10. Preserve existing user changes. Do not replace an entire Red Pitaya top-level design when a scoped module and connection change is sufficient.
11. Never call an arbitrary `.bin` release-ready. Release packaging requires
    hash-bound build evidence and passing simulation, timing, CDC, DRC, reset,
    device-tree and safe-output claims. All CLI claims are self-attested:
    offline evidence and packages always have `loadable: false`. A
    `release-candidate` carries `RELEASE_REVIEW_REQUIRED.txt` and needs a
    separate deployment-bound review receipt. Synthetic outputs must be
    `test-fixture` and carry `DO_NOT_LOAD.txt`.
12. Never promote caller-supplied flags to observed proof. Keep untrusted
    source/tool/build/verification claims under `asserted`; only deterministic
    byte parsing belongs under `observed`.
13. Treat register ABI magic and unique build identity as different evidence.
    ABI magic never proves which bitstream is loaded. A physical-output release
    needs a build identity bound to the qualified artifact and read back after
    load.
14. Positive slack is not timing closure when constraint coverage is
    incomplete. Require the top-level design summary, zero failing endpoints,
    zero unexplained unconstrained/no-clock paths, reviewed external I/O
    delays, matching generated-IP target parts, and resolved critical
    warnings/DRC findings.
15. Implement a Red Pitaya `sys_bus_if` slave against `bus.clk` and
    `bus.rstn`. Do not duplicate those nets as module ports or add a local
    reset synchronizer unless CDC/reset analysis proves it necessary. Simulate
    the first identity read after reset release and prove timely ACK.

## Route the task

Read every reference named by the applicable row. Do not rely on memory for variant-specific details.

| Task | Required reference |
|---|---|
| Decide whether work belongs to app, FPGA or deployment skill; prepare a cross-skill handoff | [../../references/workflow-map.md](../../references/workflow-map.md) |
| Create, read or update `redpitaya-project.json` | [../../references/project-contract.md](../../references/project-contract.md) |
| Compare OS releases, board profiles or cross-skill compatibility | [../../references/compatibility-matrix.md](../../references/compatibility-matrix.md) |
| Resolve conflicting evidence, inspect an unknown tree or state assumptions | [references/source-of-truth.md](references/source-of-truth.md) |
| Select board, `MODEL`, project, Vivado command or build mode | [references/board-model-and-vivado.md](references/board-model-and-vivado.md) |
| Add RTL, AXI registers, commands, status, counters or generated constants | [references/rtl-register-architecture.md](references/rtl-register-architecture.md) |
| Simulate, lint, check reset/CDC, synthesize, close timing or review reports | [references/verification-timing-cdc-reset.md](references/verification-timing-cdc-reset.md) |
| Capture events continuously, avoid re-arm gaps, add FIFO/DMA or loss accounting | [references/continuous-acquisition.md](references/continuous-acquisition.md) |
| Add an interrupt, reserved memory, custom peripheral or overlay | [references/device-tree-overlays.md](references/device-tree-overlays.md) |
| Prepare or validate an OS 2.00-35, OS 2.07-48 or OS 3.00-57 artifact | [references/os-packaging.md](references/os-packaging.md) |
| Create or audit build provenance and release gates | [references/build-evidence-and-release-gates.md](references/build-evidence-and-release-gates.md) |
| Match official acquisition registers/APIs across OS versions | [references/register-api-compatibility.md](references/register-api-compatibility.md) |
| Prepare any live-hardware verification procedure | [references/safe-hardware-testing.md](references/safe-hardware-testing.md) |

## Execute this workflow

Do the numbered stages in order. Skip a stage only when it is demonstrably outside the request, and state the skip in the handoff.

### 1. Establish the target

Locate `redpitaya-project.json` at the project root. Require:

```json
{
  "schema_version": 1,
  "target": {
    "os_release": "3.00-57",
    "model": "z10_125",
    "role": "acquisition"
  }
}
```

Use the exact shared schema at [../../schemas/redpitaya-project.schema.json](../../schemas/redpitaya-project.schema.json), OS profiles in `../../profiles/`, and [../../references/project-contract.md](../../references/project-contract.md). Use `target.os_release` and `target.model` as the requested target, then corroborate them against repository and board/image evidence. `target.model` is the live Red Pitaya profile ID, not the Vivado `MODEL` selector.

Run the shared validator before any FPGA edit:

```text
python ../../scripts/validate_redpitaya_project.py \
  <project>/redpitaya-project.json --strict
```

Resolve the audited profile-to-Vivado mapping:

```text
python scripts/resolve_fpga_target.py \
  --manifest <project>/redpitaya-project.json --json
```

The resolver marks every supported mapping probe-required. On a live target,
read the stock `overlay.sh` and use its model-selection helper. The verified OS
2.00-35 loader consumes `/opt/redpitaya/bin/monitor -f`; newer builds may
consume `/opt/redpitaya/bin/profiles -f`. Corroborate the non-empty result with
`monitor -n` (model name), `monitor -i` (model ID), the FPGA directory and
physical inventory.

Do not create a second target manifest. Preserve `project`, `application`, `deployment` and unrelated `target`/`safety` values written by other skills. This skill owns FPGA-specific updates in `fpga`. If the file is absent, create it only when project creation is in scope; otherwise report the missing contract.

Record:

- exact product and FPGA device;
- exact Red Pitaya profile and derived Vivado `MODEL` selector;
- OS release and expected loader artifact;
- repository path, branch/tag and commit;
- FPGA project (`PRJ`);
- Vivado version;
- ADC/DAC channels, sample clock and widths;
- voltage range, coupling and output-safe state;
- whether the stock FPGA path must remain available.

If any fact can cause electrical damage or an incompatible bitstream, stop before build or hardware instructions until it is resolved.

If the user supplies only a marketing name such as "STEMlab 125-14," do not
choose `z10_125` or `z20_125`. Ask for or discover the exact
`/opt/redpitaya/bin/profiles -f` value and corroborating evidence first.

### 2. Inspect the existing design

Use read-only inspection first:

1. Read the root Makefile and the exact model Tcl selected by `MODEL`.
2. Read the project Tcl, top module, constraints and relevant block-design Tcl.
3. Locate the current ADC, DAC, AXI GP/HP, interrupt and reset paths.
4. List generated versus hand-maintained sources. Never edit generated Vivado output as the only source of truth.
5. Locate address assignments and software constants.
6. Run the register-contract validator if a contract exists.
7. Identify dirty user files before changing anything.

Describe the current signal path in one short chain, for example:

```text
ADC pins -> ADC interface -> detector -> descriptor FIFO -> AXI read port -> ARM service
```

### 3. Freeze interfaces before RTL

Write down:

- clock domains and nominal frequencies;
- reset source, polarity, assertion and release behavior;
- sample format, signedness and scaling;
- event definition and dead-time/retrigger behavior;
- register base/span and access semantics;
- descriptor layout and timestamp units;
- interrupt level/pulse behavior;
- backpressure and overflow policy;
- DAC mux safe state and enable sequence.

For a new register block, copy and edit [assets/register-contract.example.json](assets/register-contract.example.json). Validate and generate all language bindings:

```text
python scripts/register_contract.py validate <register-contract.json> --strict
python scripts/register_contract.py generate <register-contract.json> \
  --out-dir <generated-dir> --strict
```

Keep the contract and generated files in the same reviewed source revision. Do
not create a Git commit unless the user explicitly authorized that repository
write. Do not hand-maintain duplicate offset lists. Every generated language
binding contains the canonical contract SHA-256; compare it across RTL and
software during review.

### 4. Implement a bounded RTL change

Prefer a small module with explicit ports over logic pasted throughout the top level.

- Use sized constants and explicit signed conversions.
- Separate combinational next-state logic from sequential state.
- Give every state, output-enable, command latch and counter a deterministic reset value.
- Synchronize control bits into the consuming clock domain.
- Transfer multi-bit data with a handshake, asynchronous FIFO, Gray counter or stable-snapshot protocol.
- Use write strobes for partial AXI writes.
- Implement pulse commands as write-triggered pulses, not persistent level bits.
- Use W1C for sticky error flags.
- Saturate diagnostic counters unless software explicitly supports rollover accounting.
- Capture related counters into a snapshot bank before the ARM reads multiple words.
- Preserve the original DAC path whenever custom override is disabled.

### 5. Verify in simulation

Create self-checking tests. At minimum test:

- power-up/reset and reset during activity;
- valid and invalid register accesses;
- command pulse exactly once;
- enable/disable and safe output;
- threshold equality and signed corner cases;
- FIFO empty, one entry, full, simultaneous push/pop and overflow;
- counter carry, saturation and coherent snapshot;
- timestamp wrap;
- clock ratios on both sides of every CDC;
- randomized stalls/backpressure;
- recovery after overflow and epoch reset.

Use assertions for invariants. The test must fail with a nonzero exit status on an incorrect result. A waveform-only test is insufficient.

### 6. Run structural and implementation gates

Run available HDL lint and simulation before Vivado. Then use the repository's exact supported flow:

```text
make sim PRJ=<project> MODEL=<model>
make PRJ=<project> MODEL=<model>
```

Use `make project PRJ=<project> MODEL=<model>` only when interactive Vivado work is required. Do not treat GUI success as a reproducible build; preserve the Tcl/source changes needed for batch reconstruction.

Require:

- synthesis and implementation complete without critical warnings;
- no unconstrained internal/generated clocks;
- timing met for every real clock;
- no unexplained combinational loop or inferred latch;
- CDC report reviewed, not merely generated;
- utilization leaves reasonable headroom;
- output artifact provenance recorded.

Do not hide a timing problem with a broad false path. Constrain only a structurally verified asynchronous or static path.

### 7. Package for the declared OS

Update the existing `fpga` object with the final project, OS-exact artifact name, overlay, loader, register-contract path, version-register address, magic and clock. Validate the entire shared contract again before packaging. Use [assets/redpitaya-project.os2.example.json](assets/redpitaya-project.os2.example.json) and [assets/redpitaya-project.os3.example.json](assets/redpitaya-project.os3.example.json) only as field examples; never overwrite another skill's valid manifest sections with them.

The build normally produces `red_pitaya.bit` and a Bootgen binary such as
`red_pitaya.bit.bin`. First create machine-bound evidence using the exact
reports from this build. Follow
[references/build-evidence-and-release-gates.md](references/build-evidence-and-release-gates.md);
do not invent passing results.

Then package the binary, never a raw `.bit`, with:

```text
python scripts/fpga_package.py package \
  --manifest <project>/redpitaya-project.json \
  --bitstream <build>/red_pitaya.bit.bin \
  --output-dir <new-package-dir> \
  --dtbo <build>/fpga.dtbo \
  --register-contract <project>/register-contract.json \
  --build-evidence <evidence>/build-evidence.json \
  [--zip]
```

Then validate:

```text
python scripts/fpga_package.py validate \
  --manifest <project>/redpitaya-project.json \
  --package-dir <package-dir>
```

The packager selects the exact OS filename and writes hashes and provenance. It
refuses a raw `.bit`, a binary without a plausible Xilinx sync/packet header,
unknown OS/model, malformed FDT overlay, missing register contract,
missing/failed build evidence, existing output directory or mismatched
package. The validator reports `classification`, `attestation`,
`release_review_required` and `loadable`. `loadable` is always false. A
`test-fixture` package is useful only for offline tool tests and is never a
deployment input. A `release-candidate` is eligible only for independent
release review; the package is not live-load permission.

### 8. Prepare hardware acceptance

Read [references/safe-hardware-testing.md](references/safe-hardware-testing.md). Build a staged procedure:

1. verify identity and backup;
2. disconnect or attenuate unsafe loads;
3. disable custom outputs;
4. load through the target OS's observed loader;
5. confirm FPGA manager state;
6. read only `MAGIC`, ABI and capabilities;
7. test register access without signal output;
8. test internal/small signals;
9. test nominal input and output under measurement;
10. force FIFO pressure and verify loss counters;
11. restore the known-good image.

Leave execution to `$deploy-red-pitaya-project` unless the user explicitly expands this task.

### 9. Hand off evidence

Use the handoff packet and permission boundaries in [../../references/workflow-map.md](../../references/workflow-map.md) when app or deployment work follows.

Report:

- target OS/model and exact repository commit;
- changed RTL, constraints, Tcl, overlay and contracts;
- register ABI version and generated bindings;
- tests run and their results;
- worst setup/hold slack, unconstrained-path count and utilization;
- package path, exact artifact filename and SHA-256;
- what was not run, especially Vivado or hardware;
- known warnings with disposition;
- safe load, stop and rollback procedure.

Never say "FPGA validated" when only source parsing or packaging was tested.

## Definition of done

An FPGA change is done only when:

- target identity and OS profile are explicit;
- RTL and software share one validated register contract;
- self-checking simulation covers normal and failure behavior;
- CDC/reset logic has a justified implementation;
- implementation timing and critical warnings are reviewed, or clearly marked not run;
- package validation passes for the requested OS;
- release metadata remains self-attested/non-loadable until a separate
  deployment-bound review and live-action authorization;
- hardware steps start safe and include rollback;
- limitations and untested claims are explicit.
