# Python or C++ service application

## Service role

Use a supervised service for work that must survive browser closure, reconnect after network loss, keep durable state, coordinate protocols, or run longer than a bounded HTTP request.

Use Python when:

- development speed, parsing, storage, networking, or analysis dominates;
- Linux scheduling is acceptable;
- dependencies can be pinned for the target Python ABI.

Use C++ when:

- the Red Pitaya C API is central;
- CPU/memory overhead matters;
- deterministic memory ownership and lower service latency justify native complexity.

Neither service architecture makes Linux hard real-time. Use FPGA logic for per-sample determinism.

## Process contract

Implement:

- foreground mode for systemd;
- loopback-only API by default;
- explicit startup self-test;
- readiness separate from process existence;
- signal handling for `SIGTERM`/`SIGINT`;
- idempotent stop;
- bounded shutdown;
- structured logs to stdout/stderr;
- atomic state persistence;
- a version endpoint;
- health/status independent of the browser.

Never daemonize under systemd or write a manually managed PID file unless the target service manager requires it.

## Python compatibility

For the supplied releases:

- OS 2.00-35 and OS 2.07-48 use Python 3.10 (`cp310`);
- OS 3.00-57 uses Python 3.12 (`cp312`).

Pure Python is more portable but still test on both interpreters. Any compiled wheel/extension is OS-, architecture-, Python-, and often glibc-specific. Prefer standard-library code or build a locked wheelhouse for each target.

Do not install packages from the network during live deployment. Build/test artifacts before installation.

## C++ compatibility

Build for ARM Zynq-7000 with the exact target sysroot. Pin:

- compiler and C++ standard;
- target architecture/float ABI;
- Red Pitaya API headers/library;
- glibc/libstdc++ symbol versions;
- board model feature flags.

Avoid throwing exceptions across C interfaces. Use RAII for hardware mappings, file descriptors, locks, and threads.

## OS 2.07 launcher compatibility

On the verified OS 2.00-35 Build 35 and OS 2.07-48 Build 48 launchers, a service-backed app can work at
its direct `/<slug>/` route yet remain absent from `/bazaar?apps=`. The stock
launcher indexed the app only after its directory contained a target-built
`controllerhf.so`. Treat this as a presentation compatibility module, not as a
reason to move service logic or hardware ownership into the native controller.

When stock-launcher visibility is an acceptance requirement:

1. Verify the behavior on the exact image and compare a shipped app.
2. Set `application.controller_binary` to `controllerhf.so`.
3. Keep the shim source under `launcher/Makefile`, `launcher/src/main.h`, and
   `launcher/src/main.cpp`.
4. Export `rp_app_desc`, `rp_app_init`, `rp_app_exit`, `UpdateSignals`,
   `UpdateParams`, `UpdateBinarySignals`, `PostUpdateSignals`,
   `PostUpdateBinarySignals`, `OnNewSignals`, and `OnNewParams`.
5. Make the shim read-only and lifecycle-only. It must not duplicate service
   state, open `/dev/mem`, load an FPGA, control outputs, or acquire signals.
6. When `fpga.enabled` is false, omit `fpga.conf` entirely and retain only an
   explicit no-op `fpga.sh`. The verified OS 2.00-35 launcher treats a literal
   `none` in `fpga.conf` as a bitstream path, returns start error `-5`, and
   suppresses the tile.
7. Build against the exact target SDK/sysroot. Never copy a shim from another
   OS release.
8. Verify ELF32, little-endian, EM_ARM, exported symbols, dynamic dependencies,
   SHA-256, and a target-side load/init/exit smoke test before packaging.
9. Package the source and exact target-built binary together. A newly added
   shim changes the package digest and therefore requires a new immutable
   deployment plan.

The validator accepts this layout for `python-service`, `cpp-service`, and
`hybrid` projects only when the manifest explicitly declares
`controllerhf.so`. The normal systemd service remains the sole owner of
durable state and hardware.

## HTTP boundary

Bind to `127.0.0.1` unless remote service access is explicitly required. Let the app’s Nginx route provide the same-origin public boundary.

Use:

- GET `/status`;
- GET `/version`;
- POST `/stop`;
- explicit POST endpoints for mutations.

Reject unsupported methods. Limit headers/body. Use JSON for structured
inputs. Default application-level authentication to `none`; do not add a token
prompt or bearer gate without the user's explicit opt-in. When
`operator-token` is selected, implement the reviewed lifecycle in
[authentication-policy.md](authentication-policy.md). Same-origin routing is
not authorization, so network exposure and physical-output gates still require
their own controls.

## Red Pitaya API initialization and read-only calls

Do not assume `rp_Init()` is a harmless per-request attach. Its behavior is
release- and API-build-dependent and may reset generator or acquisition state.
Before using any initialization entry point:

1. inspect the exact target headers, symbols and shipped examples;
2. determine which process owns initialization and `rp_Release()`;
3. record the blocks and state affected by initialization/release;
4. prefer one long-lived hardware owner for a persistent instrument; and
5. prove read-only status/capture leaves overlay identity, ASG enable/mode,
   counters and unrelated channel state unchanged.

Some OS 2.x Red Pitaya API builds expose `rp_InitReset(false)` as an
attach-without-reset path. Use it only after verifying the exact target symbol
and semantics. If it is absent, do not automatically call a resetful
initializer from status or capture. Move the operation behind the existing
hardware owner or fail clearly. Always release only resources that the current
operation actually owns.

## OS 2 custom-register access compatibility

On a verified OS 2.07-48 target, the stock
`/opt/redpitaya/bin/monitor` utility could read an exact custom AXI register
page while a long-lived Python `/dev/mem` `mmap` worker received SIGBUS on its
first read. Do not infer that a readable register in `monitor` proves that an
application's chosen mapping backend works.

For Python services that need custom registers on OS 2:

1. Keep the `/dev/mem` mapping in an expendable worker process so SIGBUS cannot
   terminate the API service.
2. Perform the first magic/ABI/capabilities/build-ID read before declaring the
   backend usable.
3. If that bounded worker dies with SIGBUS but the OS-provided `monitor`
   utility reads the exact identity successfully, use a reviewed fallback that
   spawns `monitor` once per allow-listed read/write with an absolute,
   root-owned, non-writable executable path and a short timeout.
4. Parse exactly one hexadecimal read value. Reject nonzero exit, timeout,
   malformed output, unreviewed offsets and attempts to write read-only
   registers.
5. Serialize all fallback writes, retain the single-writer lock, and issue only
   an idempotent STOP until the exact contract is proven.
6. Record the backend transition in the audit log. A successful fallback is
   degraded compatibility evidence, not permission to weaken physical-output
   authorization or watchdog gates.
7. Acceptance must prove both direct identity access and service-level
   recognition. Test output-active, watchdog and safe-zero readbacks without
   issuing START during a no-output deployment.

Prefer a corrected native MMIO backend when it is demonstrated on the exact OS
and model. Keep the bounded `monitor` backend as an explicit compatibility path,
not an unreported silent workaround.

## State and durability

Separate:

- runtime state under `/run/<service>`;
- durable configuration/history under a verified writable data path;
- application static files under `/opt/redpitaya/www/apps/<slug>`.

Write state to a temporary file, `fsync` when durability matters, and atomically replace. Version persistent formats and implement migration or fail clearly.

## Acceptance

- service starts in foreground and becomes ready;
- duplicate start does not create duplicate hardware owners;
- browser disconnect does not stop required work;
- process restart restores or safely rejects prior state;
- stop is idempotent and safe under partial initialization;
- service never listens beyond loopback unintentionally;
- OS-specific Python/native dependencies are verified;
- Nginx reports upstream failures rather than stale success.
