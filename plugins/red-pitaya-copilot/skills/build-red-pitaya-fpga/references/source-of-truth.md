# Source-of-truth and evidence policy

Use this policy whenever the board, OS, repository, loader, register set or documentation disagrees with another source.

## Contents

1. Evidence precedence
2. Required evidence record
3. Read-only discovery
4. Conflict resolution
5. Assumptions and stop conditions
6. Evidence report template

## 1. Evidence precedence

Apply this order from strongest to weakest:

1. **Observed target board**: exact profile output, `/etc/os-release`, loader script, mounted files, live device tree and hardware ID.
2. **Exact supplied OS image**: files from the named release image, including its loader and shipped overlays.
3. **Repository revision used to build the target image or application**.
4. **Documentation version matching that repository or OS release**.
5. **Current upstream documentation and current upstream repository**.
6. **General Red Pitaya knowledge, examples, forum posts and memory**.

Do not average conflicting sources. Select the higher-precedence source and document the conflict.

The observed board is authoritative only for that physical board at that moment. It does not prove another board with a similar hostname has the same model or software.

## 2. Required evidence record

Before editing, build an evidence record in task notes:

| Field | Required value | Evidence |
|---|---|---|
| Physical role | generator, acquisition, test, unknown | cabling/user statement |
| Board product | exact commercial model | profile/serial label |
| FPGA device | Z7010, Z7020, other | profile/repository part |
| Model selector | `Z10`, `Z20_14`, etc. | model Tcl and board mapping |
| OS release | exact string such as `2.07-48` | image name or board |
| Loader | exact script/path and observed syntax | loader file on same OS |
| Artifact name | `fpga.bit.bin` or `fpga.bin` | loader implementation |
| FPGA project | `v0.94`, `stream_app`, custom | project manifest |
| Repository revision | commit and dirty state | version control |
| Vivado version | normally `2020.1` | Tcl version check |
| Register ABI | magic/version/base/span | register contract |
| Safe output | disabled value and physical condition | design requirement |

If a required field cannot be observed, label it `UNKNOWN`. Never silently fill an unknown with a likely value.

## 3. Read-only discovery

Use only commands available on the target release. Common evidence includes:

```text
cat /etc/os-release
uname -a
/opt/redpitaya/bin/profiles -f
/opt/redpitaya/bin/monitor -n
/opt/redpitaya/bin/monitor -i
/opt/redpitaya/bin/monitor -f
find /opt/redpitaya/fpga -maxdepth 3 -type f
sed -n '1,240p' /opt/redpitaya/sbin/overlay.sh
cat /sys/class/fpga_manager/fpga0/state
cat /proc/device-tree/model
mount
```

Do not assume all listed utilities exist. Absence is evidence about the release, not permission to install or alter it.

For board identity, read the live stock `overlay.sh` and copy the non-empty
result of the same helper it uses to `target.model`. The verified OS 2.00-35
loader uses `monitor -f`; newer builds may use `profiles -f`. Corroborate it
with `monitor -n` (model name), `monitor -i` (model ID), the matching FPGA
directory, Zynq part and physical inventory.

For an offline image, inspect:

- `/etc/os-release`;
- `/opt/redpitaya/sbin/overlay.sh`;
- `/opt/redpitaya/fpga/**`;
- `/opt/redpitaya/bin/profiles`;
- shipped `.dtbo`/`.dtso` files;
- Red Pitaya API headers and libraries;
- systemd units that own FPGA loading.

For a repository, inspect:

- root Makefile defaults;
- `red_pitaya_vivado_<MODEL>.tcl`;
- `red_pitaya_vivado_project_<MODEL>.tcl`;
- project Tcl and constraints;
- checked-in register documentation;
- Git commit/tag and dirty files.

## 4. Conflict resolution

Use this procedure:

1. Quote or summarize both conflicting claims.
2. Identify which source is closer to the exact target.
3. Inspect the implementation when possible.
4. Adopt the implementation-observed behavior.
5. Add a compatibility note so future work does not repeat the ambiguity.
6. Make validators reject an ambiguous package instead of guessing.

Examples:

- General documentation may show `fpga.bit.bin`, while an exact OS 3.00-57 loader expects `fpga.bin`. Package as `fpga.bin` for that image.
- A web guide may name `controller.so`, while an exact image ships `controllerhf.so`. Use the image’s ABI and filename for that OS.
- The repository Makefile may default to `MODEL=Z20_G2`; that default does not identify the user’s board. Pass the selected model explicitly.
- A `.bit` file may be accepted by a GUI upload tool but not by Linux FPGA Manager. Generate and package the loader-compatible Bootgen binary.

## 5. Assumptions and stop conditions

Safe assumptions:

- A local software-only simulation may use a declared clock frequency.
- A packaging test may use a synthetic binary fixture clearly marked as non-loadable.
- A review may proceed while Vivado is unavailable if it reports implementation as not run.

Unsafe assumptions that require stopping before build/load:

- product model inferred only from hostname;
- Z7010/Z7020 inferred only from Ethernet label or case;
- ADC/DAC voltage range inferred from a screenshot;
- bitstream compatibility inferred from identical FPGA family;
- device-tree compatibility inferred from matching filename;
- OS inferred from documentation currently open in a browser;
- base address selected without checking the active design;
- output enable polarity inferred from a different project.

When blocked, continue with architecture, simulation scaffolding or a verification plan if those actions cannot create a false hardware artifact. Clearly mark the unresolved target.

## 6. Evidence report template

Use this compact format in the handoff:

```text
Target:
  role:
  product:
  FPGA:
  MODEL:
  OS:
  loader:
  expected artifact:

Build provenance:
  repository:
  commit:
  dirty files:
  PRJ:
  Vivado:

Contract:
  base/span:
  magic:
  ABI:
  safe output:

Conflicts resolved:
  - claim A:
    claim B:
    selected:
    reason:

Unknowns:
  -
```

Do not report an unknown as a default.
