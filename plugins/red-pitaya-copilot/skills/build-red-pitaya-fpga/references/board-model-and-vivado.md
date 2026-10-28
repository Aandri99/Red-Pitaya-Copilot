# Board targeting and Vivado 2020.1 workflow

Select the exact board model and build flow before generating an artifact.

## Contents

1. Supported model selectors
2. Model identification
3. Project selection
4. Vivado toolchain
5. Reproducible build modes
6. Windows and Linux details
7. Build-record requirements
8. Common failures

## 1. Supported model selectors

`target.model` in `redpitaya-project.json` is the exact board profile. The Vivado `MODEL` selector is derived from it:

| `target.model` profile | Vivado `MODEL` | FPGA | Probe required |
|---|---|---|---|
| `z10_125` | `Z10` | xc7z010 | yes |
| `z10_125_v2` | `Z10` | xc7z010 | yes |
| `z10_125_pro_v2` | `Z10` | xc7z010 | yes |
| `z20_122` | `Z20` | xc7z020 | yes |
| `z20_125` | `Z20_14` | xc7z020 | yes |
| `z20_125_4ch` | `Z20_4` | xc7z020 | yes |
| `z20_125_ll` | `Z20_ll` | xc7z020 | yes |
| `z20_125_v2` | `Z20_G2` | xc7z020 | yes |
| `z20_250` | `Z20_250` | xc7z020 | yes |
| `z20_250a` | `Z20_250` | xc7z020 | yes |
| `z20_250_1_0` | `Z20_250` | xc7z020 | yes |

Resolve this table deterministically:

```text
python scripts/resolve_fpga_target.py \
  --manifest <project>/redpitaya-project.json --json
```

The resolver reads the shared board/OS profiles and rejects an unaudited mapping. Never place the Vivado selector such as `Z10` into `target.model`.

The commercial-product mapping in the supplied official FPGA documentation is supporting context only:

The supplied official Red Pitaya FPGA documentation maps products to build selectors as follows:

| Product | `MODEL` |
|---|---|
| STEMlab 125-10 | `Z10` |
| STEMlab 125-14 | `Z10` |
| STEMlab 125-14 Gen 2 | `Z10` |
| STEMlab 125-14 PRO Gen 2 | `Z10` |
| STEMlab 125-14 Z7020 | `Z20_14` |
| SDRlab 122-16 | `Z20` |
| SIGNALlab 250-12 | `Z20_250` |
| STEMlab 125-14 4-Input | `Z20_4` |
| STEMlab 125-14 PRO Z7020 Gen 2 | `Z20_G2` |
| STEMlab 125-14 TI | `Z20_ll` |
| STEMlab 65-16 TI | `Z20_ll` |

Treat the table as a selector map, not identity evidence. Confirm the product independently.

The repository may contain additional experimental scripts such as `Z10_4_test`. Do not select an undocumented or test selector unless the task specifically targets it and its constraints/part are reviewed.

### Model-dependent properties

Do not share these blindly across model selectors:

- Zynq part and available logic/BRAM/DSP;
- ADC/DAC count, resolution and sample rate;
- ADC clock input and PLL/MMCM topology;
- analog front-end control;
- differential/single-ended constraints;
- expansion connector pinout;
- block-design ports;
- DDR interface and HP port layout;
- interrupt concatenation;
- XDC files.

## 2. Model identification

Read the live stock `overlay.sh` and use its model helper as the exact
overlay/build profile. The verified OS 2.00-35 loader uses `monitor -f`; newer
builds may use `profiles -f`. Copy the non-empty result to `target.model` and
corroborate it with `monitor -n` (model name), `monitor -i` (model ID), the
matching FPGA directory and at least one physical fact:

1. board profile/model output from the exact target or image;
2. commercial product/serial label or the user’s physical inventory;
3. Zynq part visible in the matching model Tcl;
4. known-good shipped FPGA directory for the detected board profile.

Then inspect:

```text
red_pitaya_vivado_<MODEL>.tcl
red_pitaya_vivado_project_<MODEL>.tcl
cfg/ports_<MODEL>.tcl or selected constraint sources
prj/<PRJ>/red_pitaya_top.sv
```

File names vary by repository revision. Follow the exact Tcl source list.

Never use the root Makefile’s default `MODEL` as identity. Never derive `target.model` from a marketing label alone. Always pass the resolver’s `MODEL=<value>` on build commands.

## 3. Project selection

Common active project flags are:

| Purpose | Typical `PRJ` | Model restrictions |
|---|---|---|
| Oscilloscope/generator/PID base | `v0.94` | most 125 MHz, SDR, 4-input, Gen 2/TI variants |
| 250 MHz base | `v0.94_250` | SIGNALlab 250-12 |
| Streaming/DMA-oriented base | `stream_app` | most non-250 variants |
| 250 MHz streaming | `stream_app_250` | SIGNALlab 250-12 |
| Logic analyzer | `logic` | supported 125 MHz/4-input/Gen 2/TI variants |
| 250 MHz logic | `logic_250` | SIGNALlab 250-12 |

Use the exact repository’s project table as authority; support changes by revision.

Choose:

- `v0.94` when modifying the familiar oscilloscope/generator signal path or preserving stock behavior is important.
- `stream_app` when DDR streaming and AXI master data movement are already aligned with the requirement.
- a copied custom project when changes would destabilize a shipped project.

Do not place a new project under a name that collides with a shipped project. Keep upstream files easily diffable.

## 4. Vivado toolchain

Use Xilinx Vivado **2020.1** for the documented Red Pitaya scripts. The generated block-design Tcl checks the tool version. Editing only the version guard can allow startup but does not prove IP compatibility.

If another version is unavoidable:

1. preserve a clean copy of the 2020.1-compatible tree;
2. create a dedicated migration branch;
3. open project mode;
4. review the IP status report;
5. upgrade each IP deliberately;
6. regenerate the block-design Tcl;
7. inspect every changed IP parameter and clock/reset connection;
8. run the full simulation and timing suite;
9. record the new tool build number;
10. never call the result bit-equivalent to 2020.1.

The current evidence policy admits only exact Vivado 2020.1 claims to
`release-candidate`, and those claims remain self-attested. Output from a
migrated Vivado version must not be promoted by editing the reported version
or bypassing the guard. It remains development/test-only until the skill's
version policy, expected reports and compatibility review are explicitly
updated. Even a 2020.1 candidate has `loadable: false` until independent,
deployment-bound release review.

SDK/Vitis versions are separate from the FPGA build. Do not require ARM SDK tools when changing RTL only. The repository’s `all` target may invoke `xsct` for FSBL/device tree; use a scoped FPGA target when the host lacks SDK tooling, or install the exact documented dependency only with user approval.

## 5. Reproducible build modes

### Simulation mode

Run:

```text
make sim PRJ=<project> MODEL=<model>
```

The exact simulator/testbench support varies by project. Inspect `red_pitaya_vivado_sim.tcl` and the project’s `tbn` directory. Add self-checking tests for custom logic rather than assuming the upstream top-level testbench covers it.

### Non-project batch mode

Run:

```text
make PRJ=<project> MODEL=<model>
```

The repository Makefile normally calls:

```text
vivado -nojournal -mode batch \
  -source red_pitaya_vivado_<MODEL>.tcl \
  -tclargs <PRJ> <DEFINES>
```

This is the preferred reproducible build. It typically produces:

```text
prj/<PRJ>/out/red_pitaya.bit
prj/<PRJ>/out/red_pitaya.bit.bin
```

The full `all` target may also generate FSBL and device-tree sources. Read the Makefile before claiming exact outputs.

### Project/GUI mode

Run:

```text
make project PRJ=<project> MODEL=<model>
```

The result can be opened in Vivado for ILA placement, block design inspection and interactive debug. The GUI project is derived output. Recreate all durable changes in source RTL/Tcl/XDC.

Rerunning project generation can overwrite generated project resources. Preserve hand-maintained RTL outside generated directories.

### Direct Tcl mode

If `make` is unavailable, use Vivado Tcl with the exact command from the Makefile. Do not invent argument order. Example shape:

```text
vivado -nojournal -mode batch \
  -source red_pitaya_vivado_Z10.tcl \
  -tclargs my_project ""
```

Confirm Windows quoting in the Vivado command environment.

### Bootgen conversion

The Linux FPGA Manager consumes a binary form, not the raw Vivado `.bit`. The repository normally creates it with:

```text
all:{prj/<PRJ>/out/red_pitaya.bit}
bootgen -image <bif> -arch zynq -process_bitstream bin
```

Use the repository command or equivalent Bootgen invocation. Do not rename a raw `.bit` to `.bin`.

## 6. Windows and Linux details

- Keep the repository path free of spaces.
- Prefer a short ASCII-only path.
- Initialize Git metadata; some scripts derive version information from Git.
- Run Vivado environment setup before invoking tools.
- On Windows, use the Vivado command prompt or a shell with `vivado`, `bootgen`, and optional `make` visible.
- On Linux, source the Xilinx settings script for 2020.1.
- Do not mix generated output from Windows and Linux in one build directory.
- Do not use a cloud-synced directory for active Vivado builds.
- Keep antivirus/indexers away from large generated project trees when they cause locking.

Before building:

```text
vivado -version
bootgen -help
git status --short
```

Do not run `clean` in a dirty tree until the exact generated targets and user files are distinguished. The upstream clean target is recursive and destructive.

## 7. Build-record requirements

Capture:

- host OS;
- Vivado version/build;
- repository remote, branch/tag, commit and dirty patch;
- `PRJ`, `MODEL`, `DEFINES`, `HWID` and `DTS_VER`;
- executed command;
- start/end result;
- synthesis/implementation status;
- worst negative slack, total negative slack and failing endpoints;
- unconstrained-path count;
- utilization by LUT, FF, BRAM and DSP;
- critical warnings with disposition;
- SHA-256 of raw `.bit` and Bootgen binary.

Do not use modification time as provenance.

## 8. Common failures

### `BD_TCL-109` version mismatch

Cause: wrong Vivado release. Use 2020.1 or perform a controlled IP migration. Do not just suppress the check.

### Missing project/model Tcl

Cause: invalid `PRJ`/`MODEL` pair or wrong repository revision. Check the exact project table and model scripts.

### Bitstream builds but loader rejects it

Likely causes:

- wrong model/part;
- raw `.bit` supplied instead of Bootgen binary;
- corrupted transfer;
- wrong OS filename/path;
- mismatched device-tree overlay.

### Hardware loads but registers are wrong

Check:

- address assignment versus contract;
- stale software headers;
- byte versus word offsets;
- AXI clock/reset;
- endianness and width;
- expected magic/ABI;
- a different FPGA image still loaded.

### Timing passes only in one mode

Project and batch flows may have diverged. Compare source lists, defines, constraints, IP state and strategy settings. Treat batch reconstruction as the release path.
