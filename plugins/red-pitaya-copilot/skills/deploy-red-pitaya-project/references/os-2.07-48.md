# Deployment profile: Red Pitaya OS 2.07-48

Use this profile only when the live probe or the exact image identifies Red Pitaya OS `2.07-48`.

## Verified profile facts

The analyzed stable image has these relevant characteristics:

- Ubuntu 22.04.5 userland.
- Python 3.10 generation.
- GCC 11 generation.
- glibc 2.35 generation.
- systemd 249 generation.
- Red Pitaya U-Boot based on 2022.3.
- Red Pitaya kernel branch associated with 2024.1.
- Primary packaged FPGA artifact expected by the image loader: `fpga.bit.bin`.
- Device-tree overlays are distributed as `.dtbo`; image contents include more development/source material than the analyzed 3.00-57 image.
- Web applications are located beneath `/opt/redpitaya/www/apps`.
- `/opt/redpitaya` is a separate VFAT application partition in the analyzed image and may be mounted read-only.
- Shipping web applications use `controllerhf.so`, despite some general documentation mentioning `controller.so`.

Treat these as a profile check, not a replacement for probing the board.

## Live-image caveats to probe

An exact OS 2.07-48 Build 48 board was observed with:

- `/opt/redpitaya/bin/acquire` reporting version
  `2.07-48-3afd5c148`; its local capture interface accepts a sample count,
  decimation, `--volt`, `--gain1`, `--gain2`, trigger channel and trigger
  level. Treat it as a mutable acquisition owner: prove exclusivity and
  capture/restore the relevant scope state rather than describing the command
  as passive or lossless.
- the Red Pitaya Nginx binary compiled without the standard HTTP access and
  Basic-auth modules. Therefore platform authentication cannot be assumed,
  and `allow`, `deny`, or `auth_basic` may make an app fragment invalid.
  Inspect `nginx -V` and the effective `nginx -T` output on every target.
- app fragments included directly from `../apps/*/nginx.conf`. A valid file on
  disk is not proof that a route is active; the exact target configuration
  test and post-reload request are mandatory.
- a stock-FPGA marker at `/tmp/loaded_fpga.inf`, while a custom application
  used `fpgautil` directly. A direct load need not refresh that marker, so the
  marker alone is not evidence of the currently loaded register contract.
- a custom OPD3 page that was readable with the OS-provided `monitor` utility
  while the service's isolated Python `/dev/mem` mmap worker received SIGBUS.
  Require separate direct-register and service-backend acceptance. A bounded,
  allow-listed `monitor` subprocess fallback may keep the service operational,
  but it must retain timeouts, single-writer locking, exact identity gates and
  physical-output authorization.
- the stock Nginx process, through its Red Pitaya hardware module, retaining an
  open `/dev/mem` descriptor. Classify this as a legacy bypass-capable hardware
  owner for physical-output writer-exclusivity decisions. Do not classify its
  mere presence as a blocker to installing an output-passive or
  simulation-only app; instead keep physical writes release-blocked and report
  exclusivity as unproven.
- the stock `/bazaar?apps=` launcher index omitting otherwise valid app
  directories that lacked `controllerhf.so`. `info/info.json` and icons alone
  did not make a service-backed app visible on the home screen. The direct
  `/<slug>/` route remained usable.

These observations improve the probe checklist; they are not universal facts
for every repacked 2.07 image.

## Deployment rules

### Applications

- Keep the app slug and `/opt/redpitaya/www/apps/<slug>` path stable unless the project intentionally migrates it.
- Validate `info/info.json`, launcher icons, static assets, `nginx.conf`, Lua and shell files before commit.
- Determine how the installed framework includes app Nginx fragments; do not install a separate global include unless the live system requires it.
- Declare whether acceptance requires only a direct route or also a stock
  launcher tile. For a launcher tile, prove the target-built lifecycle
  controller requirement before packaging and include the exact binary in the
  immutable plan. Never copy a controller from another OS/model or patch one
  into a committed app afterward.

### Native binaries

- Build ARM binaries and `controllerhf.so` against the OS 2-compatible sysroot/toolchain.
- Do not copy a native controller built on OS 3 into OS 2.
- Check ELF architecture and dynamic dependencies before staging.
- Treat Python extensions compiled for Python 3.12 as incompatible with Python 3.10.
- Do not depend on glibc 2.39 symbols in an OS 2 target.

### FPGA package

For this exact profile, require the primary artifact name:

```text
fpga.bit.bin
```

Before load:

1. Verify target model and project model match.
2. Read the live `overlay.sh` or equivalent and confirm it expects `fpga.bit.bin`.
3. Verify matching `.dtbo` and any loader configuration.
4. For `overlay.sh`, install and hash-match both copies:
   `/opt/redpitaya/www/apps/<slug>/` and
   `/opt/redpitaya/fpga/<model>/<slug>/`. Resolve `fpga.conf` and prove it
   points at the qualified permanent-profile copy. App-root integrity alone is
   insufficient.
5. Verify the artifact checksum and build metadata.
6. Stop/inhibit outputs.
7. Stop dependent services.
8. Load only after explicit authorization.
9. Verify FPGA magic/build/register-map version before restarting services.
10. Verify the persistent service can read that identity through its actual
    register backend. If it falls back from mmap to `monitor`, require an audit
    event and recheck STOP, output-active, watchdog and safe-zero readbacks.

Do not rename an OS 3 `fpga.bin` and assume it becomes an OS 2 artifact. Packaging format and loader expectations, not only filename, matter.

### systemd and Nginx

- Validate units using tools available in systemd 249.
- Avoid directives introduced after the installed systemd release.
- Query actual Nginx binary, effective config path and service unit before choosing test/reload commands.

## OS 2 acceptance checklist

- Probe still reports OS `2.07-48`.
- Board model equals manifest.
- `/opt/redpitaya` original mount state restored.
- App files match plan hashes.
- Permanent FPGA profile files match both plan and app-root hashes, and the
  effective `fpga.conf` target has the qualified bitstream hash.
- OS 2 native binaries resolve all dynamic dependencies.
- Nginx config test passes and app routes return expected content type/status.
- If launcher visibility is required, `/bazaar?apps=` contains the exact slug
  and the home screen shows its tile; otherwise record the direct app URL.
- Service starts without repeated restart.
- If FPGA changed: loader consumed `fpga.bit.bin`, device-tree overlay is correct, signature matches and outputs default disabled.
- Rollback archive includes the prior OS 2 artifact and overlay files.

## Cross-version migration warning

Moving a project from OS 2 to OS 3 is not an in-place file copy. Rebuild native components, repackage FPGA output, retest Nginx/Lua/service integration, and create a new probe/plan for the OS 3 target.
