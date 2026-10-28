# OS-aware FPGA packaging

Package an already generated Bootgen binary for the exact Red Pitaya OS release. Packaging does not convert or validate the electrical correctness of a bitstream.

## Contents

1. Shared project contract
2. OS profiles
3. Required FPGA fields
4. Source artifact requirements
5. Package layout
6. Packager commands
7. Validation behavior
8. Release checklist

## 1. Shared project contract

Use the plugin-root schema:

```text
../../schemas/redpitaya-project.schema.json
```

and OS/model profiles:

```text
../../profiles/os-2.00-35.json
../../profiles/os-2.07-48.json
../../profiles/os-3.00-57.json
../../profiles/board-models.json
```

The project file is always named:

```text
redpitaya-project.json
```

Validate it before and after changing FPGA fields:

```text
python ../../scripts/validate_redpitaya_project.py \
  <project>/redpitaya-project.json --strict
```

Paths above are relative to this skill directory. Resolve absolute paths when invoking from another working directory.

Do not create a competing FPGA-only project manifest. The application, FPGA and deployment skills share this file.

## 2. OS profiles

The supplied images and exact live OS 2.00 target establish:

| Property | OS 2.00-35 | OS 2.07-48 | OS 3.00-57 |
|---|---|---|---|
| Linux userspace | Ubuntu 22.04.4 | Ubuntu 22.04.5 | Ubuntu 24.04.4 |
| Python | 3.10 | 3.10 | 3.12 |
| GCC major | 11 | 11 | 13 |
| glibc | 2.35 | 2.35 | 2.39 |
| systemd | 249 | 249 | 255 |
| loader | `/opt/redpitaya/sbin/overlay.sh` profile-name interface | same path; inspect interface | same path; inspect interface |
| FPGA artifact | `fpga.bit.bin` | `fpga.bit.bin` | `fpga.bin` |
| device tree | `fpga.dtbo` | `fpga.dtbo` | `fpga.dtbo` |
| acquisition interrupts/timestamps/16-bit API | not exposed in matching profile | not exposed in matching profile | exposed in matching profile |

OS 2.00-35 and 2.07-48 are one artifact family. OS 3 is a separate
packaging family. Loader arguments still differ within OS 2; inspect the live
script rather than copying commands between releases.

## 3. Required FPGA fields

The shared schema requires:

```json
{
  "fpga": {
    "enabled": true,
    "project": "my_acquisition",
    "artifact_name": "fpga.bin",
    "device_tree_name": "fpga.dtbo",
    "loader": "overlay.sh",
    "register_contract": "fpga/register-contract.json",
    "version_register": "0x40600004",
    "magic_value": "0x52504631",
    "clock_hz": 125000000
  }
}
```

Rules:

- OS 2.00-35 or OS 2.07-48 with FPGA enabled: `artifact_name` must be exactly `fpga.bit.bin`.
- OS 3.00-57 with FPGA enabled: `artifact_name` must end in `.bin` but not `.bit.bin`; use exactly `fpga.bin`.
- `device_tree_name` is `fpga.dtbo` when a custom/matching overlay is packaged; use `null` only when the exact deployment intentionally uses an existing overlay.
- `loader` is `overlay.sh` for the normal supported flow, `fpgautil` only for an explicitly engineered low-level flow, or `none` only with FPGA disabled.
- `register_contract` points to the source contract relative to project root.
- `version_register` must identify the ABI version register’s absolute address.
- `magic_value` is the value expected from the contract’s `MAGIC` register.
- `clock_hz` is the application timestamp/sample clock, not automatically the marketing sample rate.

`target.model` is the exact board profile such as `z10_125` or `z20_125_v2`, not the Vivado `MODEL` flag. Resolve the profile-to-build-selector mapping in [board-model-and-vivado.md](board-model-and-vivado.md).

After FPGA design changes:

1. update `fpga.project`;
2. set OS-exact `fpga.artifact_name`;
3. set matching overlay name/null;
4. set loader;
5. set contract path;
6. set ABI register address/magic/clock;
7. validate the complete shared contract;
8. only then package.

## 4. Source artifact requirements

Accept only a structurally plausible Bootgen Zynq binary. The offline parser
requires an aligned Xilinx 7-series sync word and a plausible configuration
write header, but these checks do not authenticate the producer. Expected
source names commonly include:

```text
red_pitaya.bit.bin
red_pitaya_top.bit.bin
```

Reject:

- raw `.bit`;
- `.bin` created by renaming a `.bit`;
- zero/truncated files;
- artifact from an unknown commit;
- artifact built with another model;
- artifact whose timing did not pass;
- artifact with a stale/mismatched overlay.

The packager rejects a source ending exactly in `.bit`, a renamed text blob and
a malformed configuration prefix. It still cannot determine internal board
compatibility or prove Vivado/Bootgen provenance from opaque binary bytes, so
hash-bound build evidence and independent release review are mandatory. Read
[build-evidence-and-release-gates.md](build-evidence-and-release-gates.md).

Generate the binary through repository Bootgen flow:

```text
echo all:{red_pitaya.bit} > red_pitaya.bif
bootgen -image red_pitaya.bif -arch zynq -process_bitstream bin
```

Use exact repository syntax and output. Do not use shell examples blindly on Windows.

## 5. Package layout

The bundled packager creates:

```text
<package-dir>/
  fpga.bit.bin OR fpga.bin
  fpga.dtbo
  register-contract.json
  redpitaya-project.json
  build-evidence.json
  simulation-report.txt
  timing-report.txt
  cdc-report.txt
  drc-report.txt
  DO_NOT_LOAD.txt                 test fixtures only
  RELEASE_REVIEW_REQUIRED.txt     release candidates only
  package-manifest.json
  SHA256SUMS
```

`package-manifest.json` records:

- package schema;
- target OS/profile model and derived Vivado `MODEL`;
- FPGA project;
- artifact names;
- hashes and byte sizes;
- source input names;
- release-candidate or test-fixture classification;
- `attestation: "self-attested"`;
- `release_review_required: true` only for release candidates;
- `loadable: false` for every package created by offline tooling;
- contract magic/version address;
- deterministic generation marker.

`SHA256SUMS` is sorted. A ZIP made with `--zip` has stable ordering and timestamps so identical inputs produce identical archive bytes.

The package is staging material. It is not a bootable image, is not installed
automatically and never grants permission to load the FPGA. A
release-candidate requires a separate deployment-bound engineering-review
receipt and a separate live-action authorization.

## 6. Packager commands

### OS 2 example

```text
python scripts/fpga_package.py package \
  --manifest project/redpitaya-project.json \
  --bitstream build/red_pitaya.bit.bin \
  --dtbo build/fpga.dtbo \
  --register-contract project/fpga/register-contract.json \
  --build-evidence evidence/os2/build-evidence.json \
  --output-dir dist/project-os2-fpga \
  --zip
```

Output bitstream: `fpga.bit.bin`.

### OS 3 example

Use a manifest with `target.os_release` set to `3.00-57` and `fpga.artifact_name` set to `fpga.bin`:

```text
python scripts/fpga_package.py package \
  --manifest project/redpitaya-project.json \
  --bitstream build/red_pitaya.bit.bin \
  --dtbo build/fpga.dtbo \
  --register-contract project/fpga/register-contract.json \
  --build-evidence evidence/os3/build-evidence.json \
  --output-dir dist/project-os3-fpga \
  --zip
```

Output bitstream: `fpga.bin`. This is a filename adaptation of an already valid Bootgen binary, not raw bitstream conversion.

The output directory and ZIP must not already exist. This prevents accidental overwrite. Create a new versioned output path.

Publication uses a same-directory atomic rename. On Windows, antivirus and
indexing can briefly hold a newly written directory; the packager retries only
`PermissionError` for a short bounded interval and aborts if the source
disappears or the destination appears. Do not replace this with copy/delete or
an unbounded retry, because that would weaken the all-or-nothing package
contract.

### Validate

```text
python scripts/fpga_package.py validate \
  --manifest project/redpitaya-project.json \
  --package-dir dist/project-os3-fpga
```

Validation is read-only.

## 7. Validation behavior

The package validator checks:

- shared manifest structure needed by FPGA packaging;
- known OS release;
- known target board profile;
- exact expected artifact filename;
- expected file exists and is nontrivial;
- raw `.bit` is absent;
- Xilinx `0xAA995566` sync and a plausible type-1 write header are present;
- package manifest target matches requested target;
- all recorded SHA-256 hashes and sizes;
- no unrecorded regular files;
- DTBO flattened-device-tree v17 header, bounds, reserve map and token stream;
- register contract validates;
- packaged project manifest exactly matches input;
- ZIP, if created, can be independently hashed but is not needed for directory validation.

These are structural checks. They do not authenticate Vivado/Bootgen as the
producer. See
[build-evidence-and-release-gates.md](build-evidence-and-release-gates.md)
for the exact self-attestation and external-review boundary.

It cannot prove:

- bitstream matches the FPGA part;
- timing closure;
- overlay fragments resolve against a live base tree;
- register magic is actually synthesized;
- electrical safety;
- hardware behavior.

State these limitations in every release handoff.

## 8. Release checklist

- Shared schema validation passes with `--strict`.
- `target.os_release` equals the actual target image.
- `target.model` equals the exact profile, not marketing guess.
- Vivado `MODEL` mapping is recorded separately.
- FPGA fields are updated, not stale app defaults.
- Bootgen binary came from passing implementation.
- Artifact name is exact for OS.
- Overlay source/binary is from same build.
- Register contract validates in strict mode.
- Package and ZIP output paths are new.
- Package validation passes.
- SHA-256 is reported.
- Known-good rollback package exists before deployment.
- No credentials or target password are present.
- Package reports `attestation: self-attested`,
  `release_review_required: true` and `loadable: false`.
- Independent release review is recorded outside the package and bound to the
  immutable deployment plan, target identity and package hash.
- No claim of hardware validation is made from packaging alone.
