# Deployment profile: Red Pitaya OS 3.00-57

Use this profile only when the live probe or the exact image identifies Red Pitaya OS `3.00-57`.

## Verified profile facts

The analyzed stable image has these relevant characteristics:

- Ubuntu 24.04.4 userland.
- Python 3.12 generation.
- GCC 13 generation.
- glibc 2.39 generation.
- systemd 255 generation.
- Red Pitaya U-Boot based on 2026.1.
- Red Pitaya release/kernel generation associated with 2026.1.
- The actual image `overlay.sh` expects the primary FPGA artifact `fpga.bin`.
- Device-tree content uses `.dtbo` and `.dtso` packaging in the analyzed image.
- Web applications are located beneath `/opt/redpitaya/www/apps`.
- `/opt/redpitaya` is a separate VFAT application partition in the analyzed image and may be mounted read-only.
- Shipping web applications use `controllerhf.so`.

These image facts override generic documentation where it describes `.bit.bin` for every modern release or `controller.so` for every app.

## Deployment rules

### Applications

- Preserve app slug and route compatibility unless migration is explicit.
- Revalidate Nginx/CORS behavior; OS 3 image configuration differs from older images in some areas.
- Do not assume an OS 2 Lua or native controller is ABI-compatible merely because static files work.

### Native binaries

- Build for the exact ARM architecture and OS 3-compatible sysroot.
- Rebuild `controllerhf.so` and other compiled extensions for glibc 2.39/Python 3.12 as applicable.
- OS 3 binaries using newer glibc symbols are not backward compatible with OS 2.
- Inspect ELF architecture, interpreter, required shared libraries and unresolved symbols before staging.

### FPGA package

For this exact profile, require the primary artifact name:

```text
fpga.bin
```

Before load:

1. Verify exact target model.
2. Read the live loader and confirm `fpga.bin`, overlay and directory requirements.
3. Validate `.dtbo`/`.dtso` relationships for the deployed design.
4. Verify build ID, checksum, Vivado/project model and register-map version.
5. Assert safe stop and stop dependent services.
6. Load only after explicit authorization.
7. Verify the new FPGA signature and default output-disabled state.
8. Start dependent services only after the expected register contract responds.

Do not rename an OS 2 `fpga.bit.bin` and deploy it as `fpga.bin`.

### Acquisition capability awareness

OS 3 headers/register documentation add or expose acquisition features such as:

- Acquisition interrupt masks/status.
- Per-channel trigger and fill interrupts.
- Global, trigger and stream-buffer timestamps.
- Additional diagnostics.
- 16-bit acquisition mode.
- Updated calibration/API behavior.

A service using these features must declare its OS 3 requirement. Acceptance must verify interrupt/timestamp behavior and not only that the process starts.

### systemd and Nginx

- Validate units against systemd 255 on the target.
- Do not backport an OS 3 unit to OS 2 without reviewing directives.
- Query the actual Nginx command/config/service path and perform a config test before reload.

## OS 3 acceptance checklist

- Probe still reports OS `3.00-57`.
- Board model equals manifest.
- `/opt/redpitaya` original mount state restored.
- App files match plan hashes.
- Native binaries resolve OS 3 dependencies.
- Nginx configuration and expected CORS/JSON routes behave correctly.
- systemd units reach stable active state without restart loops.
- If FPGA changed: loader consumed `fpga.bin`, overlays match, signature/register map match and outputs default disabled.
- Acquisition interrupt/timestamp/16-bit features used by the project pass feature-specific tests.
- Rollback includes prior OS 3 FPGA/overlay and configuration files.

## Cross-version migration warning

An application that works on OS 2 may require native rebuilds, Python updates, API source changes, Nginx behavior validation and new FPGA packaging. Create a new OS 3 deployment plan rather than editing the OS field in an old plan.
