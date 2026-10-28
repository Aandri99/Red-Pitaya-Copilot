# Application validation and acceptance

## Contents

1. Validation levels
2. Static checks
3. Contract tests
4. Service/native tests
5. Target tests
6. Evidence report

## Validation levels

Run in order:

1. structure and manifest;
2. language syntax;
3. pure unit tests;
4. local API/service tests;
5. target-OS build/runtime tests;
6. safe board integration;
7. authorized hardware/output tests.

Do not skip directly to physical output as a substitute for lower-level tests.

## Static checks

Run:

```text
python scripts/validate_red_pitaya_app.py <app> --os-release 3.00-57 --backend nginx-lua
python ../../scripts/validate_redpitaya_project.py <app>/redpitaya-project.json
node --check <app>/js/app.js
python -m py_compile <app>/service/app_service.py
```

The non-strict commands are the truthful offline build gate. Preserve and
report unresolved warnings such as an unknown target hostname. Once exact
target evidence and required native artifacts exist, run:

```text
python scripts/validate_red_pitaya_app.py <app> --deployment-ready
python ../../scripts/validate_redpitaya_project.py <app>/redpitaya-project.json --strict
```

Also use target-compatible:

- Lua/LuaJIT parse check;
- C/C++ compiler with warnings-as-errors;
- `readelf` for ELF architecture/dependencies;
- exact Nginx config test.

The bundled validator checks manifest consistency, architecture files, routes/actions, unsafe text patterns, service ports/units, placeholders, icons, and native ELF basics. It does not replace target runtime tests.

For an OS 2.07 service launcher shim, also prove:

- the direct route works before and after installation;
- `/bazaar?apps=` contains the exact app slug exactly once after installation;
- the binary is ELF32, little-endian, and EM_ARM;
- all lifecycle, parameter/signal, and binary-signal callbacks are present;
- target-side load/init/exit succeeds;
- the shim performs no FPGA, output, acquisition, or `/dev/mem` operation;
- an app-only package omits `fpga.conf`, and `/bazaar?start=<slug>` does not
  attempt to resolve a fake `none` bitstream path;
- launcher discovery does not replace or duplicate the systemd service;
- switching away from and back to the app leaves the service healthy.

For a generator/acquisition instrument, also prove that read-only operations
are truly non-mutating:

- snapshot overlay/build identity, generator enable/mode/configuration,
  service epoch and persistent counters;
- apply a bounded test waveform only when physical output is authorized;
- call status and capture repeatedly during at least ten seconds of continuous
  observation;
- require the generator state and measured loopback to persist;
- require capture to reset or stop only the acquisition resources it owns; and
- verify Stop separately, returning to a confirmed disabled state.

Before deployment handoff, create and revalidate a deterministic directory
bundle as described in [packaging.md](packaging.md):

```text
python scripts/package_red_pitaya_app.py create \
  --app <app> \
  --output <new-artifact-parent>/<application-slug>
python scripts/package_red_pitaya_app.py validate \
  --package <new-artifact-parent>/<application-slug>
```

The second command reruns both validators and verifies canonical inventory,
receipt, marker, and optional transport-ZIP integrity. Warnings remain
deployment blockers even when package integrity passes.

## Contract tests

For each endpoint test:

- correct request/response;
- wrong method;
- unknown action;
- missing/extra/wrong-type fields;
- boundary values and just-outside values;
- NaN/infinity/huge body;
- duplicate command ID;
- concurrent requests;
- dependency unavailable;
- timeout after possible acceptance;
- service restart/epoch change.

Assert both HTTP status and JSON error code.

Also test the UI/API gate matrix independently:

| Case | Expected result |
| --- | --- |
| `authentication: none` | No token field, token request, bearer header or token-disabled button |
| `operator-token`, token absent | Only declared protected actions are blocked; exact access reason is shown |
| API version mismatch | Compatibility reason is shown; it is not described as an authentication failure |
| FPGA/register contract unknown | Hardware actions remain locked; read-only status still works |
| No capture requested | Graph says not requested, not “token missing” or “hardware offline” |
| Capture returned zero samples | Graph says empty capture with the acquisition error/source |
| Configured preview only | Clearly labeled reconstructed/never measured |
| Physical authorization absent | Only physical-output actions are blocked; application access remains a separate fact |

For every disabled action, assert that a visible reason is rendered next to the
control. Do not accept a generic `locked` state when multiple gates exist.

## Service/native tests

- lifecycle callback/order;
- partial initialization cleanup;
- graceful SIGTERM;
- crash recovery;
- resource exclusivity;
- listener restricted to loopback;
- repeated start/stop;
- memory/file-descriptor/thread stability;
- durable state corruption/storage full;
- native symbol and dynamic-library resolution.

For `controllerhf`, test app switching because the framework may unload the previous module.

When tests create temporary state on Windows, create the directory first and
apply POSIX permission bits only when `os.name == "posix"`. A numeric POSIX
mode passed during directory creation is not a portable Windows ACL policy and
can make cleanup or subsequent access unreliable. Guard every recursive test
cleanup by resolving the exact child directory, rejecting symlinks, and
checking a test-specific name prefix.

For HTTP/1.1 rejection paths, verify that the service either consumes the full
declared request body or sets `close_connection = True` before replying.
Otherwise unread bytes can be parsed as a second request and create request
desynchronization, confusing logs, or unsafe handler behavior.

When an instrument capture fails and state restoration also fails, assert that
the reported error preserves both failures and marks restoration uncertain.
Never let the primary acquisition exception hide a failed restore. Avoid broad
reset commands unless the complete state they change is versioned, captured,
restored and semantically read back.

Test authorization status separately from start authorization. An expired,
wrong-board, too-short, or already-consumed authorization must never be shown
as ready even if its JSON structure is valid.

When one output override is disabled but alternate output producers exist,
assert that the API reports electrical safety as unknown. UI completion
messages must name the verified override and must not claim an electrically
safe connector.

## Target tests

Before board changes, use `$deploy-red-pitaya-project` for backup/staging/rollback. On target, verify:

1. board model and OS match manifest;
2. files and hashes match staged outputs;
3. exact language/runtime versions;
4. Nginx includes the route and passes config test;
5. service unit verifies and starts;
6. static page returns 200;
7. when launcher visibility is required, the exact slug occurs once in
   `/bazaar?apps=`, the tile opens the intended route, and no backup or helper
   service appears as a duplicate tile;
8. repeated status/version/health/capture calls preserve overlay identity,
   generator state, counters and unrelated channel state;
9. status distinguishes each confirmed-disabled producer from electrical
   output safety; unknown remains unknown;
10. stop is idempotent;
11. app/service restart returns safe;
12. only then run authorized hardware tests.

## Evidence report

Report each check as:

```text
check:
command/tool:
environment:
expected:
observed:
result: pass | fail | not-run
reason if not-run:
artifact/log:
```

Do not say "fully tested" if Lua, native ABI, target Nginx, service install, or
hardware behavior was not exercised.

Test the packaging tool itself after modifying its code or contract:

```text
python -m unittest -v scripts/test_package_red_pitaya_app.py
```

The suite covers deterministic ZIP output, no-overwrite behavior, cache
exclusion, secret rejection, Windows reparse points, symlinks when the host
permits creating them, transient Windows rename failures, warning-blocked
packages, and payload/receipt/archive tampering.
