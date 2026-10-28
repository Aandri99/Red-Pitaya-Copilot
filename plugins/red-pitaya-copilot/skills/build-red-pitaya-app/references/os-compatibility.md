# OS 2.x and OS 3.x compatibility

## Contents

1. Evidence policy
2. Observed release profiles
3. Portable and non-portable artifacts
4. Migration procedure
5. Compatibility record

## Evidence policy

Use evidence in this order:

1. exact live target;
2. exact supplied/extracted OS image;
3. official documentation matching that release;
4. general Red Pitaya guidance.

Record contradictions instead of silently selecting the convenient answer.

On a live board, read the stock `overlay.sh` and use the same model helper. The
verified OS 2.00-35 loader uses `monitor -f`; newer builds may use
`profiles -f`. Corroborate it with `monitor -n`, `monitor -i`, the matching
FPGA directory and physical inventory. Store the exact profile ID, never a
translated marketing name. Stop and investigate inconsistent evidence. Use
`../../references/compatibility-matrix.md` and
`../../references/project-contract.md` for shared cross-skill policy.

## Observed release profiles

The supplied images and live OS 2.00 target were identified as:

| Property | OS 2.00-35 | OS 2.07-48 | OS 3.00-57 | Application consequence |
| --- | --- | --- | --- | --- |
| Ubuntu base | 22.04.4 | 22.04.5 | 24.04.4 | OS 3 has a different runtime/toolchain base |
| Python | 3.10 | 3.10 | 3.12 | Compiled modules require separate OS 3 builds |
| GCC generation | 11 | 11 | 13 | Rebuild native executables/libraries |
| glibc | 2.35 | 2.35 | 2.39 | OS 3 binary may require symbols absent on OS 2 |
| systemd | 249 | 249 | 255 | Use directives supported by the target |
| app location | `/opt/redpitaya/www/apps` | same | same | Source layout can be shared |
| app partition | read-only VFAT observed | commonly same | commonly same | Deployment must be transactional |
| native app artifact observed | `controllerhf.so` | `controllerhf.so` | `controllerhf.so` | Verify exact target ABI |
| FPGA package observed | `fpga.bit.bin` | `fpga.bit.bin` | `fpga.bin` | OS 2 is one artifact family; OS 3 differs |

These are profiles for the supplied stable images, not guarantees for every custom/repacked image.

Use the plugin-owned machine-readable profiles as the canonical duplicated facts:

- `../../profiles/os-2.00-35.json`
- `../../profiles/os-2.07-48.json`
- `../../profiles/os-3.00-57.json`
- `../../profiles/board-models.json`

Do not create a second app-local OS profile.

Official documentation says OS 2.00+ uses `fpga.sh`. The verified OS 2 targets
share `fpga.bit.bin`, but their `overlay.sh` argument interfaces differ. The
official web-app tutorial describes `controller.so`, while shipped apps contain
`controllerhf.so`. Inspect the target.

The live OS 2.00-35 launcher also proved that absence of `fpga.conf` is the
no-specific-FPGA signal. A file containing `none` is parsed as a bitstream path,
causes `/bazaar?start=<slug>` error `-5`, and can suppress the launcher tile.
App-only packages must omit the file; do not replace live evidence with a
generic marker convention.

## Portable artifacts

Usually portable after validation:

- HTML, CSS, and browser JavaScript;
- JSON metadata and schemas;
- pure Lua using modules present in both targets;
- pure Python compatible with 3.10 syntax and standard-library behavior;
- source code and tests;
- Nginx fragments using directives/modules present in both target builds.

Not portable without a separate build/check:

- `controllerhf.so` and any `.so`;
- C/C++ executables;
- CPython extension modules or compiled wheels;
- FPGA images/device-tree overlays across board models or OS packaging families;
- code depending on exact Red Pitaya API symbols;
- systemd units using version-specific directives;
- scripts relying on image-specific paths/services.

## Migration procedure

1. Pin source and current manifest.
2. Inventory every executable, `.so`, Python dependency, Nginx directive/module, unit file, helper path, and hardware API call.
3. Replace copied native outputs with reproducible builds.
4. Compare the target’s canonical shipped app/service.
5. Build against the destination target/sysroot.
6. Validate ELF architecture and dynamic symbol versions.
7. Run syntax/unit/service tests on the destination release.
8. Verify application discovery and Nginx inclusion.
9. Verify safe stop before start/output.
10. Run authorized hardware tests and preserve rollback evidence through `$deploy-red-pitaya-project`.

For Gen 2/TI hardware, respect official minimum OS and calibration constraints. Do not downgrade or cross-load a bitstream because the app UI appears compatible.

## Compatibility record

For each native artifact record:

```text
artifact:
source commit:
target OS:
target model:
architecture/ABI:
compiler/sysroot:
RP API version:
ELF interpreter/dependencies:
hash:
test evidence:
```

“Works on Red Pitaya” is not a compatibility record.
