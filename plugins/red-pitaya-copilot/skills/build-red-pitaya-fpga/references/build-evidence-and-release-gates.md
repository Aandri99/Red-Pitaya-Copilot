# FPGA build evidence, trust boundaries and release review

## Purpose

A file named `fpga.bin` is not proof of a valid, compatible or safe FPGA
image. This workflow binds an artifact to a project manifest, register
contract, device tree, source claim, tool-version claim and verification
reports. It produces one of two offline classifications:

- `release-candidate`: a self-attested candidate that passed the tool's
  consistency gates and must receive a separate deployment-bound engineering
  review before any live load;
- `test-fixture`: synthetic or incomplete material used only for offline tool
  tests.

Neither classification is load authorization. The offline evidence writer and
packager always emit `loadable: false`.

Run commands in this document from `<fpga-skill-dir>`, the directory containing
this skill's `SKILL.md`, unless a command explicitly says otherwise.

## Trust model

Evidence schema version 2 deliberately separates claims from observations:

| JSON area | Source | Meaning |
|---|---|---|
| `asserted` | command-line values supplied by the operator/automation | untrusted claims awaiting engineering review |
| `observed` | deterministic parsing performed by this tool | limited structural facts about the exact bytes |
| `artifacts` and `reports` | file hashing and byte counts performed by this tool | identity and integrity of the files |
| `target` | project manifest plus bundled profiles | requested target, not live-board identity |
| `attestation` | fixed by the tool | always `self-attested` |
| `release_review_required` | fixed by classification | `true` only for `release-candidate` |
| `loadable` | fixed by the offline tool | always `false` |

The tool never treats `--timing-result pass`, `--source-dirty false`,
`--vivado-version 2020.1`, or similar caller values as independently verified.
They appear under:

```text
asserted.origin = "caller-supplied-untrusted-claims"
```

Do not move an asserted field into `observed`. Do not add a CLI option that can
set `attestation`, `release_review_required` or `loadable`.

## Inputs from one build

Keep these together:

1. validated `redpitaya-project.json`;
2. exact Bootgen binary produced from that revision;
3. compiled `fpga.dtbo`;
4. strict register-contract source;
5. self-checking simulation report;
6. implemented timing summary;
7. CDC report and review result;
8. implementation DRC/critical-warning report;
9. repository revision and dirty-state claim;
10. exact `PRJ`, Vivado `MODEL`, FPGA part, build command, Vivado version and
    Bootgen version claims.

If a file came from another run, stop and recreate the evidence.

## What the offline parser actually observes

The bitstream parser requires:

- an aligned Xilinx 7-series `0xAA995566` sync word within the first 1 MiB;
- a plausible type-1 write-packet header shortly after the sync word;
- the release-candidate truncation-screen minimum of 512 KiB (1 KiB for a test
  fixture). This is deliberately below the verified 995,936-byte XC7Z010
  Bootgen image; file size is not a device-compatibility proof.

The DTBO parser requires:

- a complete 40-byte flattened-device-tree header;
- header `totalsize` equal to the file size;
- FDT version 17 and bounded, aligned, non-overlapping sections;
- a terminated memory-reservation map;
- a parseable structure token stream with one root, balanced nodes, valid
  property-name references and an `FDT_END` token;
- at least one property.

These checks reject renamed text files, trivial magic-only blobs, truncation
and many corrupt artifacts. They do **not** prove that Vivado or Bootgen
produced the bytes, that the device tree is compatible with a live kernel, or
that the image targets the declared FPGA.

Likewise, a software-readable register `MAGIC` proves only that the loaded
design claims one ABI. It does not bind the loaded programmable logic to a
unique source revision or bitstream hash. A professional physical-output
release needs a build identity that is unique to the qualified artifact (for
example a generated digest/build ID register included in the hashed register
contract), plus deployment evidence that reads and matches that identity after
load. Never use ABI magic alone as loaded-image provenance.

## Self-attested release-candidate gates

The tool requires these caller assertions before it will label evidence a
`release-candidate`:

| Asserted gate | Required value |
|---|---|
| Source revision | 7-64 hexadecimal revision characters |
| Dirty source | `false` |
| Vivado | exactly `2020.1` for this audited suite |
| Bootgen version | non-empty |
| Simulation | `pass` |
| Timing | `pass`, WNS >= 0 ns, TNS >= 0 ns |
| Unconstrained paths | `0` |
| Critical warnings | empty list |
| CDC review | `pass` |
| DRC | `pass` |
| Device-tree compile | `pass` |
| Reset review | `pass` |
| Output reset/default state | disabled |
| Target | exact manifest OS/profile and audited Vivado `MODEL` |
| FPGA part | contains the Zynq device required by the board profile |

These are consistency gates, not a trust anchor. A human or trusted CI system
must review the exact hashed reports and build provenance later.

The independent review must use the top-level **Design Timing Summary**, not a
passing sub-clock or path-group row. It must also verify all of the following:

- setup and hold failing-endpoint counts are zero;
- internal unconstrained endpoints are zero;
- no-clock checks are zero or each intentional static/asynchronous exception
  has a narrow, source-linked waiver;
- missing input/output-delay counts are zero or each intentionally
  asynchronous connector has a reviewed interface waiver;
- invalid or empty clock/false-path object queries are absent;
- generated IP, checkpoints and the implemented design all target the exact
  declared FPGA part;
- I/O placement warnings and implementation DRC findings are resolved or
  explicitly accepted by a deployment-bound engineering review.

Positive WNS/TNS with incomplete constraint coverage is not timing closure.
A warning-count summary is not a DRC or constraints review.

### Stock housekeeping derived-clock trap

The classic Red Pitaya housekeeping RTL can clock `DNA_PORT` from
`i_hk/dna_clk_reg/Q`, a fabric register that divides the housekeeping input
clock by eight. Vivado can otherwise report:

- `no_clock`: `i_hk/dna_clk_reg/Q`;
- unconstrained maximum-delay endpoints:
  `i_hk/i_DNA/READ` and `i_hk/i_DNA/SHIFT`.

Do not ignore those findings merely because they are inherited vendor logic or
because the PD/data path has positive slack. Declare the exact generated-clock
relationship using the implemented hierarchy and re-run `check_timing
-verbose`. On the verified classic block, the READ/SHIFT decodes can change
only during the low half-cycle, but generic STA cannot infer that value
correlation. If a hold-only exception is required, constrain only the two
decoded source registers to the two DNA control pins, keep setup analysis
enabled, explain the counter-phase proof beside the XDC, and retain the
before/after reports. Never apply a clock-domain-wide false path.

### Vivado policy

Only Vivado **2020.1** output can enter the current suite as a
`release-candidate`. A design migrated with another Vivado release may be
developed and tested, but must remain incomplete/test-only until this policy,
its parsers, expected reports and compatibility matrix are deliberately
updated and reviewed. Editing a Vivado version guard or writing `2020.1` in the
CLI does not satisfy that review.

## Create evidence

Use a new output path. Substitute values observed from the exact build:

```text
python scripts/fpga_build_evidence.py create \
  --manifest <project>/redpitaya-project.json \
  --bitstream <build>/red_pitaya.bit.bin \
  --dtbo <build>/fpga.dtbo \
  --register-contract <project>/register-contract.json \
  --output-dir <new-evidence-directory> \
  --classification release-candidate \
  --built-at-utc <YYYY-MM-DDTHH:MM:SSZ> \
  --repository <repository-identifier> \
  --revision <exact-commit> \
  --source-dirty false \
  --vivado-version 2020.1 \
  --bootgen-version <observed-version> \
  --prj <exact-PRJ> \
  --model <exact-Vivado-MODEL> \
  --part <exact-Xilinx-part> \
  --build-command "<exact reproducible command>" \
  --simulation-report <report> \
  --timing-report <report> \
  --cdc-report <report> \
  --drc-report <report> \
  --simulation-result pass \
  --timing-result pass \
  --wns-ns <observed-number> \
  --tns-ns <observed-number> \
  --unconstrained-paths 0 \
  --cdc-result pass \
  --drc-result pass \
  --dtc-result pass \
  --reset-review-result pass \
  --outputs-default-disabled true
```

Repeat `--critical-warning "<exact unresolved warning>"` for every unresolved
critical warning. A candidate is rejected when this list is non-empty. Never
hide or manually edit a report to satisfy the tool.

The output directory contains exactly:

```text
build-evidence.json
simulation-report.txt
timing-report.txt
cdc-report.txt
drc-report.txt
```

## Validate after any move or rebuild

```text
python scripts/fpga_build_evidence.py validate \
  --evidence <evidence>/build-evidence.json \
  --manifest <project>/redpitaya-project.json \
  --bitstream <build>/red_pitaya.bit.bin \
  --dtbo <build>/fpga.dtbo \
  --register-contract <project>/register-contract.json
```

Validation recomputes hashes and `observed` parser results. Any mismatch is a
blocker. Recreate evidence; never repair hashes or observed fields manually.

## Package markers and deployment handoff

The packager carries the same top-level safety fields into
`package-manifest.json`:

```json
{
  "classification": "release-candidate",
  "attestation": "self-attested",
  "release_review_required": true,
  "loadable": false
}
```

A release-candidate package contains `RELEASE_REVIEW_REQUIRED.txt`. A
test-fixture contains `DO_NOT_LOAD.txt` and has
`release_review_required: false`. Marker removal or metadata editing never
changes the trust state because all payload files are hash-bound.

The deployment workflow must:

1. reject `test-fixture`;
2. reject `loadable: true` produced by offline metadata;
3. require and verify a separate `fpga_release_reviewed` receipt bound to the
   deployment ID, immutable plan hash, target machine identity, SSH host-key
   fingerprint and exact package hash;
4. obtain a distinct live-load authorization after review.

The review must inspect the actual hashed source/reports and target identity.
The package itself never authorizes `overlay.sh`, `fpgautil`, FPGA-manager
writes or output enable.

For a physical-output image, the review must also bind:

1. the packaged bitstream hash;
2. the unique build identity compiled into that bitstream;
3. the live identity read after loading;
4. the exact target part/profile;
5. the physical-output authorization and rollback plan.

If the design exposes only a generic ABI magic, classify loaded-image identity
as unproven and block output enable.

## Interpretation limits

- Hashes prove byte identity, not correctness or origin.
- Structural parsers prove only the checks documented above.
- The helper does not semantically parse every Vivado report format.
- Caller-supplied pass flags are self-attested, even when their report copies
  are hash-bound.
- Board compatibility, electrical limits, live safe state and rollback remain
  deployment-time responsibilities.
- `release-candidate` means “ready for independent release review,” not
  “approved,” “trusted,” “loadable” or “safe.”
