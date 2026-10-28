# Red Pitaya compatibility matrix

Use this document when choosing a build path. Do not guess from a product photo,
marketing name, previous board, or another project's filenames.

## Evidence order

When two sources disagree, use this order:

1. A read-only probe from the exact target board.
2. Files inspected in the exact Red Pitaya OS image used by that board.
3. Official documentation for that exact release and board profile.
4. General Red Pitaya documentation.
5. An existing project only after its target and release are proven identical.

Record the commands and relevant output in the project evidence folder. Never put
passwords, private keys, tokens, or complete environment dumps in evidence files.

## Supported release profiles

| Contract value | OS image | Userspace baseline | Python | GCC | FPGA package name | Native app rule |
|---|---|---|---:|---:|---|---|
| `2.00-35` | Live-derived ecosystem 2.00 build 35 | Ubuntu 22.04.4 | 3.10 | 11 | `fpga.bit.bin` | OS 2.x artifact family; stock loader accepts a profile name and resolves files below `/opt/redpitaya/fpga/<model>/<profile>/` |
| `2.07-48` | Red Pitaya OS 2.07-48 stable | Ubuntu 22.04.5 | 3.10 | 11 | `fpga.bit.bin` | Build `controllerhf.so` and other native binaries for this exact target ABI |
| `3.00-57` | Red Pitaya OS 3.00-57 stable | Ubuntu 24.04.4 | 3.12 | 13 | `fpga.bin` | Rebuild native binaries; do not reuse an OS 2 binary |

The inspected inputs are pinned so another engineer can reproduce the same
profile analysis:

| Release | Supplied archive | SHA-256 |
|---|---|---|
| `2.07-48` | `RedPitaya_OS_2.07-48_stable.img (1).zip` | `0EA2E14587FAFFC5C47001EFA0E3ACAE23553A37B9AC05C92ACD7AAE24719E77` |
| `3.00-57` | `RedPitaya_OS_3.00-57_stable.img (1).zip` | `EA90398468BAFA0C6A4CD422BE67C592185F110D1329D5E91FD8918726BDFDF3` |

The `2.00-35` profile was derived from an exact live-board probe rather than an
offline archive. Its stock loader, `fpgautil` contract and `fpga.bit.bin`
layout were inspected directly. The stock `z10_125/v0.94/fpga.dtbo` SHA-256
was also verified byte-identical to the inspected OS 2.07-48 copy. Its
provenance is in `profiles/os-2.00-35.json` and its operational rules are in
the deployment reference for that release.

These archives were inspected offline on 2026-07-29. A live probe still takes
precedence because a target can run a different image or locally modified
files.

All current profiles use the following app-stack locations:

- app root `/opt/redpitaya/www/apps`;
- mount point `/opt/redpitaya`, normally read-only;
- FPGA device-tree overlay filename `fpga.dtbo`;
- loader `/opt/redpitaya/sbin/overlay.sh`;
- Nginx service `redpitaya_nginx.service`;
- native controller filename `controllerhf.so`.

OS 2.00-35 and OS 2.07-48 share the same deployable FPGA artifact names. Their
`overlay.sh` command interfaces differ: the verified 2.00 loader accepts one
profile name, while newer OS 2 releases may accept richer arguments. Probe and
use the exact live interface; never replace the vendor loader. Treat those as
adapter details inside one OS 2.x family, not as separate application or FPGA
register-ABI designs. OS 1.x and OS 3.x remain distinct compatibility families.

The matching JSON profiles in `profiles/` are the machine-readable source used by
the tools. If this table and a JSON profile ever disagree, stop and repair the
plugin before building.

## Board-model rule

Read the exact target's stock `/opt/redpitaya/sbin/overlay.sh` and use the same
helper it uses to select the model directory. The verified OS 2.00-35 loader
uses `/opt/redpitaya/bin/monitor -f`; newer releases may use
`/opt/redpitaya/bin/profiles -f`. Copy the non-empty exact result into
`target.model`. Corroborate it with `monitor -n` (model name), `monitor -i`
(model ID), the physical label and the matching FPGA directory. The supported
identifiers are listed in `profiles/board-models.json`.

The identifier selects the FPGA project, constraints, channel count, clocking and
Zynq device. A bitstream built for a different identifier is not a portable file.
Do not load it merely because both boards are labelled STEMlab.

## Application architecture decision

Choose exactly one `application.architecture`:

| Value | Use when | Build consequence |
|---|---|---|
| `static` | Browser assets only; no privileged API | No target-native binary |
| `lua-api` | Small Nginx/Lua status and register API | Keep write routes explicit and authenticated by the surrounding deployment |
| `native-controller` | Existing Red Pitaya controller ABI is required | Target-build `controllerhf.so`; never copy between OS 2 and OS 3 |
| `service` | Long-running Python or native worker | Provide a systemd unit, health command and idempotent stop |
| `hybrid` | Browser UI plus a worker/service and optional FPGA | Validate every interface boundary and service dependency |
| `none` | FPGA-only project | App routes must be empty and app-only files are omitted |

Prefer the least complex architecture that satisfies the instrument. Do not add a
service solely to serve static files.

## FPGA packaging decision

The raw Vivado bitstream is not the deployable package.

1. Confirm `target.model`.
2. Build the matching FPGA project and constraints.
3. verify timing and inspect critical warnings.
4. Convert/package the bitstream using the exact OS profile:
   - OS `2.00-35` or `2.07-48`: package contains `fpga.bit.bin`;
   - OS `3.00-57`: app package contains `fpga.bin`.
5. Compile the matching overlay as `fpga.dtbo`.
6. Include and validate `registers.json`.
7. Require the app to read a documented magic/version register before any write.

Never rename an arbitrary bitstream to satisfy this table. The filename is only
one compatibility gate; model, Vivado project, constraints, register map and
device tree must also match.

## Deployment decision

Deployment is a transaction:

1. probe;
2. compare probe to `redpitaya-project.json`;
3. validate the package locally;
4. stage in `/tmp`;
5. verify staged hashes and contents;
6. remount `/opt/redpitaya` read-write for the shortest possible interval;
7. back up the existing app;
8. install by a same-filesystem rename;
9. restore read-only state in a guaranteed cleanup step;
10. restart only affected services;
11. run health and identity checks;
12. roll back automatically if a gate fails.

Do not deploy when the probed OS release or model differs from the manifest.
Do not run a physical output test unless the manifest allows it and the user has
separately authorized that test.

## API capabilities that differ

The OS 3 profile records support for newer acquisition capabilities including
interrupt-wait handling, 16-bit acquisition mode and trigger timestamps. The OS 2
profile treats those capabilities as unavailable. Code must branch on the
selected profile and still verify the function or symbol on the live target.

Absence of a capability is a design constraint, not permission to silently
replace event-driven acquisition with a lossy polling loop. State the limitation
and choose an explicit buffering strategy.

## Official documentation anchors

Use these as supporting references, then confirm the exact image and board:

- [Creating Your First Web App](https://redpitaya.readthedocs.io/en/latest/developerGuide/software/build/webapp/firstApp.html)
- [Manual Application Installation](https://redpitaya.readthedocs.io/en/latest/developerGuide/software/app_development/webapp/manualApps.html)
- [Overlay Utility](https://redpitaya.readthedocs.io/en/latest/appsFeatures/command_line_tools/utils/overlay_util.html)
- [Advanced FPGA Loading](https://redpitaya.readthedocs.io/en/latest/developerGuide/fpga/advanced/fpga_advanced_loading.html)
- [Tutorial helper and OS-specific bitstream extensions](https://redpitaya.readthedocs.io/en/latest/developerGuide/fpga/fpga_tutorials/configure_fpga.html)

Documentation pages evolve and sometimes describe a generic or newer release.
That is why the exact-image and live-board evidence remains higher precedence.
