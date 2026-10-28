---
name: build-red-pitaya-app
description: Design, scaffold, extend, diagnose, package, and validate professional Red Pitaya web and Linux applications for pinned OS 2.00-35, OS 2.07-48, or OS 3.00-57 targets. Use for static launcher apps, Nginx/Lua JSON APIs, native controllerhf WebSocket apps, persistent Python or C++ services, hybrid service-backed dashboards, Red Pitaya application lifecycle work, `/opt/redpitaya/www/apps`, `info/info.json`, `nginx.conf`, `controllerhf.so`, systemd units, browser APIs, or migration of an existing Red Pitaya app between OS releases. Treat OS 2.00 and 2.07 as one FPGA artifact family with release-specific loader interfaces. Route FPGA implementation to `$build-red-pitaya-fpga` and live installation or rollback to `$deploy-red-pitaya-project`.
---

# Build Red Pitaya App

Produce an application whose target, architecture, interfaces, ownership, safe state, and test evidence are explicit. Never infer compatibility from a hostname, file name, or another Red Pitaya board.

## Mandatory completion report

Before every final response, read and follow
[../../references/completion-report.md](../../references/completion-report.md).
Apply it even for read-only, diagnostic, partial or blocked work.

## Non-negotiable rules

1. Read the existing `redpitaya-project.json` before editing. Follow [../../references/project-contract.md](../../references/project-contract.md) and validate it against [../../schemas/redpitaya-project.schema.json](../../schemas/redpitaya-project.schema.json). For a new project, create it with the app scaffolder or [../../scripts/new_redpitaya_project.py](../../scripts/new_redpitaya_project.py).
2. Pin `target.os_release` to a supported exact profile and record the exact `target.model`. OS 2.00-35 and 2.07-48 both use the OS 2.x `fpga.bit.bin` plus `fpga.dtbo` artifact family; probe the live `overlay.sh` interface before selecting how the profile is installed or loaded.
3. Inspect a live target or matching image before relying on executable names, controller ABI, Nginx prefix, service names, FPGA loader names, or writable paths. Use [../../references/compatibility-matrix.md](../../references/compatibility-matrix.md), [../../profiles/os-2.00-35.json](../../profiles/os-2.00-35.json), [../../profiles/os-2.07-48.json](../../profiles/os-2.07-48.json), [../../profiles/os-3.00-57.json](../../profiles/os-3.00-57.json), and [../../profiles/board-models.json](../../profiles/board-models.json) as versioned baselines, then confirm them on target.
4. Keep browser presentation, HTTP/WebSocket transport, service logic, hardware access, and FPGA logic in separate modules.
5. Put long-running work in a supervised board-side process. A browser timer is not persistence.
6. Report board state; never render a requested state as if the board confirmed it.
7. Make Stop/Disable idempotent before implementing Start/Enable.
8. Never copy `controllerhf.so`, Python extension modules, or other native binaries between OS releases.
9. Do not load FPGA artifacts or perform live deployment in this skill. Hand those tasks to the dedicated skills.
10. Treat supplied official documentation as guidance and the exact target image/live board as authority when they disagree.
11. Package only with `scripts/package_red_pitaya_app.py`. Treat its receipt as self-attested offline evidence, never as deployment authorization. A ZIP is transport-only.
12. Default `application.authentication` to `none`. Never invent an operator token, token prompt, token file, bearer header, or token-gated button. Use `operator-token` only when the user explicitly requests application-level access control, and keep it separate from deployment receipts and physical-output authorization.
13. Keep `/dev/mem` out of the HTTP/WebSocket process. Use a bounded,
    supervised MMIO worker so an AXI abort/SIGBUS becomes a fail-closed
    `hardware_unavailable` state instead of killing the service.
14. A loopback service must close runtime-owned workers when listener binding
    fails and must support immediate restart after WebSockets close. Prove the
    proxied API returns HTTP 200 within a bounded readiness deadline;
    `systemctl is-active` alone is insufficient and may precede socket bind.
15. Treat every status, version, health and capture operation advertised as
    read-only as a hardware invariant. It must not reload the FPGA, reset the
    ASG, disable an output, clear counters, change acquisition ownership or
    mutate durable configuration. Prove this on the exact target; a successful
    JSON response is not evidence that the call was non-mutating.
16. For launcher-managed instruments, prove lifecycle stability separately
    from route reachability and tile discovery. Repeated status/capture calls,
    app switching and at least ten seconds of observation must not cause an
    unexpected Bazaar unload, overlay reload, service restart or output loss.
17. For every operator-adjustable critical setting, generate a versioned
    `protocols/critical-parameters.json` catalog, explain the setting and its
    consequences in the UI, and require a distinct review before Apply. A
    preset may fill the editor but must never mutate the board by itself.

## Route required references

Read [../../references/workflow-map.md](../../references/workflow-map.md) for cross-skill routing, permissions, stop conditions, and handoff packets. Read [references/architecture-selection.md](references/architecture-selection.md) for every new application or architecture change. Then read exactly the selected architecture:

- Static: [references/architecture-static.md](references/architecture-static.md)
- Nginx/Lua: [references/architecture-nginx-lua.md](references/architecture-nginx-lua.md)
- Native controller: [references/architecture-controllerhf.md](references/architecture-controllerhf.md)
- Python/C++ service: [references/architecture-service.md](references/architecture-service.md)
- Hybrid: [references/architecture-hybrid.md](references/architecture-hybrid.md)

Also read:

- [references/visual-design.md](references/visual-design.md) for every new app
  interface or substantial visual redesign. Use the Red Pitaya company palette
  by default unless the user explicitly supplies another brand system.
- [references/os-compatibility.md](references/os-compatibility.md) for every build, migration, native binary, service, or image-specific task.
- [references/app-lifecycle.md](references/app-lifecycle.md) when startup, ownership, concurrency, shutdown, or restart behavior matters.
- [references/nginx-websocket.md](references/nginx-websocket.md) for Nginx, HTTP, streaming, or WebSocket work.
- [references/authentication-policy.md](references/authentication-policy.md) for every app with controls, credentials, disabled actions, or remote access.
- [references/api-safety.md](references/api-safety.md) before adding or changing any action that mutates board state.
- [references/critical-parameter-review.md](references/critical-parameter-review.md) for every instrument with configurable signal-processing, acquisition, calibration, timing, FPGA, deployment, or physical-output parameters.
- [references/persistent-services.md](references/persistent-services.md) for Python/C++ processes, long profiles, journals, or boot persistence.
- [references/instrument-generator-acquisition.md](references/instrument-generator-acquisition.md) for waveform generators, OUT-to-IN loopback, calibrated acquisition, remote capture helpers, or an app whose status/capture path must preserve a running generator.
- [references/testing.md](references/testing.md) before declaring completion.
- [references/packaging.md](references/packaging.md) before creating, validating, transporting, or handing off an application bundle.
- [references/limitations.md](references/limitations.md) before making compatibility, timing, continuity, accuracy, or safety claims.

## Execute the workflow

### 1. Establish evidence

Record:

- intended board role and physical signal direction;
- exact board model, generation, FPGA part, and fast-I/O channel count;
- OS release and image build;
- current app slug and install path;
- active Nginx command line/config prefix;
- existing app/controller/FPGA owner;
- persistence and restart requirements;
- writable runtime and durable-data paths;
- safe output state and whether hardware tests are authorized.

For board model identification, read the live stock `overlay.sh` and use the
same helper it uses. The verified OS 2.00-35 loader uses `monitor -f`; newer
builds may use `profiles -f`. Use `monitor -n`, `monitor -i`, the matching FPGA
directory and physical inventory as corroboration. Record disagreements and
stop before choosing target-dependent artifacts. Use the exact profile ID,
never a marketing name.

If any item cannot be discovered, mark it `unknown` and block only the action that depends on it. Do not convert `unknown` into an assumption.

If the exact `target.model` is not supplied or discovered, stop and ask for
probe evidence. Never copy `z10_125`, a hostname, or any other identity value
from an example merely to make validation pass.

### 2. Select one architecture

Use this order:

1. `static` when no board mutation or private data is required.
2. `nginx-lua` for short, bounded, same-origin status/actions using facilities present in the target Nginx.
3. `python-service` for scheduling, state, storage, protocols, or analysis best implemented in Python.
4. `cpp-service` for lower overhead, C/C++ APIs, or stricter latency requirements that remain Linux-scheduled.
5. `hybrid` when Nginx/Lua must remain a narrow authenticated/validated adapter while a service owns durable work.
6. `controllerhf` only when compatibility with Red Pitaya's native parameter/signal WebSocket framework is a requirement.

Do not choose `controllerhf` merely because an old example uses it. Do not choose Lua for loops, blocking subprocesses, acquisition drains, or persistent state.

#### Stock web-launcher visibility is a separate requirement

Do not treat a successful `/<slug>/` response as proof that the Red Pitaya home
screen lists the app. Record both requirements explicitly:

1. direct route loads and its API/service is healthy;
2. the stock `/bazaar?apps=` index contains the exact slug and its tile opens
   the intended route.

On the verified OS 2.00-35 Build 35 and OS 2.07-48 Build 48 launchers, a service-backed app required a
target-built `controllerhf.so` before it appeared in `/bazaar?apps=`. When
launcher visibility is required for a `python-service`, `cpp-service`, or
`hybrid` app, use the passive launcher layout and target-build procedure in
[references/architecture-service.md](references/architecture-service.md).
For an app with `fpga.enabled: false`, omit `fpga.conf` entirely. On the
verified OS 2.00-35 launcher, a literal `none` is interpreted as a bitstream
path: `/bazaar?start=<slug>` returns error `-5` and the app is omitted from the
launcher list. Keep `fpga.sh` as an explicit no-op.
Keep the service as the sole state/hardware owner. For `static` or `nginx-lua`,
either obtain explicit direct-route-only acceptance or reselect an architecture
that can honestly include the required native launcher module. Do not hide a
native executable inside an app still classified as static.

Do not generalize this exact rule to every OS 2.x/3.x image. Probe the target
launcher and compare a shipped app. Never copy `controllerhf.so` across boards
or OS releases.

### 3. Scaffold deterministically

Run:

```text
python scripts/new_red_pitaya_app.py \
  --slug my_app \
  --title "My App" \
  --os-release 3.00-57 \
  --model z10_125 \
  --backend nginx-lua \
  --authentication none \
  --output <workspace>
```

Omitting `--authentication` is equivalent to `--authentication none`.
`--authentication operator-token` is an explicit policy declaration, not a
complete or automatically trusted security implementation; its transport,
scope, storage, rotation, restart and failure behavior must still be reviewed
and tested.

For `python-service`, `cpp-service`, or `hybrid`, also provide an explicitly reserved loopback port:

```text
--service-port 9187
```

The number above is syntax only, not a recommended global default. Record the
chosen port in `application.service_port`. Before deployment, inspect the exact
target read-only with `ss -ltnp`, inspect existing Red Pitaya service units and
app manifests, and prove the port is unused. If the board is unavailable, label
the port provisional and do not call the app deployment-ready.

For a Python-backed best-effort live status stream, add:

```text
--websocket
```

This option is valid only for `python-service` and `hybrid`. It generates the
service handler, exact Nginx upgrade route, browser reconnect/heartbeat logic,
and `protocols/websocket-v1.json`. Do not expose a `/ws` route manually unless
all four pieces and their validator checks are present. The generated channel
accepts no state-changing command and is not durable event storage.

Supported backends are `static`, `nginx-lua`, `controllerhf`, `python-service`, `cpp-service`, and `hybrid`. Use `--force` only after resolving the exact destination and confirming replacement is intended. The scaffolder moves the old folder to a timestamped sibling backup instead of deleting it; report that backup path.

The scaffolder returns the recoverable `backup_path` in its JSON output. Preserve
that value in the handoff whenever `--force` was used.

The generated `redpitaya-project.json` is the shared cross-skill contract. Backend names map to schema architecture values:

| Scaffold backend | Manifest `application.architecture` |
| --- | --- |
| `static` | `static` |
| `nginx-lua` | `lua-api` |
| `controllerhf` | `native-controller` |
| `python-service` | `service` |
| `cpp-service` | `service` |
| `hybrid` | `hybrid` |

Update the manifest in the same change whenever the target, architecture, routes, service name, controller binary, Python ABI, FPGA relationship, deployment policy, or safety contract changes. Do not add app-local fields outside the shared schema.

### 4. Extend an existing app without erasing its contract

1. Copy or inspect the complete app, not isolated files.
2. Preserve the folder slug unless the user explicitly authorizes a migration.
3. Create or reconcile `redpitaya-project.json`.
4. Classify every executable and `.so` by architecture, OS ABI, and origin.
5. Trace every browser action through Nginx/controller/service to its side effect and response.
6. Preserve unrelated behavior and user changes.
7. Add the safe stop path first.
8. Add cache-busting for changed static assets.
9. Update tests and validation evidence.

When adopting an old app, do not silently delete `fpga.sh`, `fpga.conf`, controller binaries, or routes. Explain their discovered purpose. For a verified app-only launcher, remove `fpga.conf` in a new versioned package because its presence can trigger an attempted bitstream load; route real FPGA ownership to `$build-red-pitaya-fpga`.

### 5. Implement observable state

Every non-static app must expose enough status to distinguish:

- process reachable versus hardware ready;
- access allowed versus action blocked, including the exact blocked reason;
- FPGA/register contract known versus unknown;
- requested mode versus confirmed mode;
- safe/disabled versus enabled/energized;
- configured/reconstructed preview, simulated data, and measured ADC samples;
- acquisition not requested, in progress, empty, unavailable, and completed;
- startup, running, stopping, stopped, and faulted;
- configuration version and application version;
- target OS/model mismatch;
- last error and last successful update time.

Use a stable JSON envelope:

```json
{
  "ok": true,
  "status": {
    "service": "ready",
    "access": {
      "mode": "none",
      "allowed": true,
      "reason": null
    },
    "hardware_ready": false,
    "hardware_ready_reason": "FPGA register contract is unknown",
    "acquisition": {
      "state": "not_requested",
      "sample_count": 0,
      "source": "none"
    },
    "safe_state": true,
    "version": "0.1.0",
    "server_time": "2026-07-29T12:00:00Z"
  }
}
```

Return an HTTP error status and `{ "ok": false, "error": { "code": "...", "message": "..." } }` for failures. Do not return HTTP 200 with a hidden failure string.

Do not use one `locked` boolean to hide why a control is unavailable. Compute
and display independent reasons for busy state, access policy, API
compatibility, hardware readiness and physical-output authorization. A missing
operator token may block only protected mutations in an explicitly
`operator-token` app; it must not erase read-only status or masquerade as an
FPGA/acquisition failure.

### 6. Validate locally

For an app with critical configurable parameters, validate and render the
operator review contract first:

```text
python scripts/critical_parameter_review.py validate <app-folder>
python scripts/critical_parameter_review.py review <app-folder>
```

When `/api/configure` exists, require a read-only `/api/parameters` route and
prove repeated catalog reads do not mutate hardware state. Derive editor
limits, presets, and displayed timing/rate values from the catalog. Invalidate
the review after any edit and keep Stop available throughout the workflow.

For an offline build-quality check, run:

```text
python scripts/validate_red_pitaya_app.py <app-folder>
```

Warnings such as an unknown hostname are truthful unresolved target evidence;
report them and continue only with work that does not depend on that evidence.
Do not invent values to silence warnings.

Immediately before packaging for a known target or handing off to deployment,
run the deployment-readiness form:

```text
python scripts/validate_red_pitaya_app.py <app-folder> --deployment-ready
```

Use `--os-release` and `--backend` in automation to assert the expected contract. Also run the shared validator:

```text
python ../../scripts/validate_redpitaya_project.py <app-folder>/redpitaya-project.json --strict
```

Then run:

- `node --check` for every JavaScript file;
- `python -m py_compile` and unit tests for Python;
- the target-compatible Lua parser for Lua;
- the target or matching sysroot build for native code;
- service-level tests with a temporary loopback port;
- browser/API tests without enabling physical outputs.

On a live target, perform launcher acceptance separately from route
acceptance: fetch the direct route, fetch `/bazaar?apps=`, prove the exact slug
occurs exactly once, open that tile, and confirm the service and hardware state
remain healthy after switching away and back. A missing or duplicate tile is a failed application acceptance
criterion when stock-launcher visibility was requested.

Warnings are unresolved engineering decisions. They are permitted in the
offline build result but block the deployment-ready result. Do not suppress
them without documenting why.

### 7. Package a deterministic directory bundle

After local validation, read [references/packaging.md](references/packaging.md)
and create a new bundle whose final directory name exactly equals the
application slug:

```text
python scripts/package_red_pitaya_app.py create \
  --app <app-folder> \
  --output <new-artifact-parent>/<application-slug> \
  --os-release 3.00-57
```

Use `--transport-zip <new-path>.zip` only when a reproducible transfer
container is needed. The ZIP is never a deployment source. Validate the exact
directory before handoff:

```text
python scripts/package_red_pitaya_app.py validate \
  --package <new-artifact-parent>/<application-slug> \
  --require-deployment-ready
```

Warnings produce an integrity-valid but deployment-blocked directory with
`DEPLOYMENT_BLOCKED.txt`. Do not edit a package in place to remove blockers;
resolve the source/target evidence and create a new package. Neither a
warning-free receipt nor a transport ZIP grants live deployment permission.

### 8. Hand off across skills

For FPGA source, register maps, bitstreams, device trees, or deterministic acquisition, invoke `$build-red-pitaya-fpga` with the same `redpitaya-project.json`.

For live board writes, service installation, Nginx reload, mount changes, FPGA loading, backups, or rollback, invoke `$deploy-red-pitaya-project`. Hand it the validated directory bundle, never the transport ZIP. Do not reproduce deployment commands from memory.

Use the handoff packet defined in [../../references/workflow-map.md](../../references/workflow-map.md). Include the validated manifest path, source path, artifact hashes when present, target evidence, safe-stop contract, tests, unresolved warnings, and explicit authorization state.

### 9. Report completion

State:

- project and app paths;
- target OS release and model;
- selected architecture and reason;
- API/WebSocket/service contracts;
- generated or changed files;
- exact validation commands and results;
- directory-bundle receipt and payload-inventory hashes;
- deployment readiness, unresolved warning blockers, and the explicit fact that offline packaging grants no deployment authorization;
- transport ZIP hash when created and the explicit fact that it is transport-only;
- native artifacts intentionally not built;
- hardware actions intentionally not performed;
- known limitations;
- direct-route and stock-launcher visibility results as separate facts;
- next FPGA or deployment handoff.
