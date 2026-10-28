# Deployment profile: Red Pitaya OS 2.00-35

Use this profile only when a live probe identifies Red Pitaya OS `2.00-35`.
This is a live-derived OS 2.x compatibility profile. It uses the same
`fpga.bit.bin` plus `fpga.dtbo` artifact family as OS 2.07-48, with an older
profile-name-only `overlay.sh` interface.

## Exact observed platform

The profile was derived on 2026-07-30 from `rp-f08e87.local`:

- ecosystem `2.00-aff683518`, build `35`;
- Ubuntu 22.04.4, Linux 5.15.0-xilinx, ARMv7;
- glibc 2.35, GCC/G++ 11.4, Python 3.10.12 and systemd 249;
- model evidence `STEMlab 125-14 v1.0`, monitor family `z10_125`;
- `/opt/redpitaya` mounted from `/dev/mmcblk0p1` as read-only VFAT;
- Nginx 1.19.10 at `/opt/redpitaya/sbin/nginx`, prefix
  `/opt/redpitaya/www`, service `redpitaya_nginx.service`;
- Nginx includes `../apps/*/nginx.conf` and `../apps/*/nginx2.conf`;
- shipped applications use ARM ELF32 little-endian `controllerhf.so`;
- the native SDK is `/opt/redpitaya/rp_sdk`;
- the observed launcher omits service-backed applications that lack a
  loadable `controllerhf.so`;
- the launcher treats a present `fpga.conf` as a bitstream-path instruction.
  For an app-only package, a literal `none` caused start error `-5` and
  suppressed the app from `/bazaar?apps=`; absence of the file is the observed
  no-specific-FPGA contract.
- `/opt/redpitaya/sbin/overlay.sh` selects the model with `monitor -f`, accepts
  one profile name, and calls `fpgautil` with
  `/opt/redpitaya/fpga/<model>/<profile>/fpga.bit.bin` plus `fpga.dtbo`;
- the stock `z10_125/v0.94/fpga.dtbo` SHA-256 is
  `dd09b17e87dab07e5344441cc09e11156660ab109cd5a8aa90dc76da47a3cea6`,
  byte-identical to the inspected OS 2.07-48 copy.

The exact SSH fingerprint and machine ID belong in the normalized probe and
deployment plan, not in a reusable manifest.

## Application rules

1. Set `target.os_release` to exact `2.00-35`.
2. Use `fpga.bit.bin` and `fpga.dtbo` when `fpga.enabled` is true. Treat
   bitstream/model/register compatibility as separate release gates.
3. Use Python ABI `3.10` for Python services.
4. Build every native file, including a passive launcher
   `controllerhf.so`, on the exact target or a proven matching sysroot.
5. Do not reuse a `controllerhf.so` built for 2.07-48 or 3.00-57.
6. Treat a working direct route and a launcher tile as separate acceptance
   tests. Verify both `/app-slug/` and `/bazaar?apps=`.
7. Keep launcher controllers passive when a systemd service owns the
   application. They must not load an FPGA, write output registers, bind the
   service port or create a second state owner.
8. For `fpga.enabled: false`, reject a packaged `fpga.conf`, including one that
   contains `none`; keep only an explicit no-op `fpga.sh`.
9. Validate Nginx with the target binary and prefix before reload.
10. Restore `/opt/redpitaya` to its original read-only state after commit or
   rollback.

## FPGA rules

1. Install a custom profile as an exact directory below
   `/opt/redpitaya/fpga/<observed-model>/<profile>/` containing
   `fpga.bit.bin` and `fpga.dtbo`.
2. Hash both the app-packaged copy and installed profile copy before load.
3. Invoke the stock loader with exactly one profile-name argument:
   `/opt/redpitaya/sbin/overlay.sh <profile>`.
4. Do not pass custom bitstream or DTBO paths to this old loader interface.
5. Do not replace `overlay.sh`, `fpgautil`, or the stock model directory.
6. Keep FPGA loading and physical-output authorization as separate gates.
7. After load, read only the expected magic, ABI, capabilities and build ID;
   confirm the output-disabled/safe-zero state before any other write.

## Acceptance

- exact hostname, MAC suffix, machine ID and SSH fingerprint still match;
- `controllerhf.so` is ARM ELF32 little-endian and exposes the callbacks
  required by the live SDK;
- app-only payload contains no `fpga.conf`; an FPGA-enabled app points
  `fpga.conf` at its exact installed OS 2.x `fpga.bit.bin`;
- `/bazaar?apps=` contains the exact slug and the launcher renders its tile;
- the direct application route loads;
- Nginx configuration is valid and remains active;
- the application service is active without restart loops;
- status/version routes identify the deployed version and exact board;
- physical outputs remain disabled;
- when FPGA changed: loader consumed the expected profile, artifact hashes
  match, magic/ABI/build ID match, and hardware reports output disabled;
- `/opt/redpitaya` is read-only again;
- backup, staged hashes, live hashes and rollback evidence are retained.
