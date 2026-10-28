# Application architecture selection

## Contents

1. Decision sequence
2. Comparison matrix
3. Ownership boundaries
4. Rejection rules
5. Manifest contract

## Decision sequence

Answer in order:

1. Does the app mutate board or filesystem state?
   - No: start with `static`.
   - Yes: continue.
2. Is each request short, bounded, and available through target Nginx/Lua facilities?
   - Yes: consider `nginx-lua`.
   - No: continue.
3. Must work continue after the browser closes or network disconnects?
   - Yes: use a service or hybrid.
4. Is Python adequate for rate, latency, and dependencies?
   - Yes: use `python-service`.
   - No: use `cpp-service`.
5. Must the app use Red Pitaya’s native parameter/signal WebSocket framework?
   - Yes: consider `controllerhf`, after proving the target ABI.
6. Must a narrow Lua adapter protect a durable service or integrate app-local routes?
   - Yes: use `hybrid`.

FPGA-deterministic behavior is not an application architecture. Route it to `$build-red-pitaya-fpga`; let the app consume a documented register/service contract.

## Comparison matrix

| Architecture | Best fit | Persistence | Hardware access | Main risks |
| --- | --- | --- | --- | --- |
| `static` | Help, visualization, offline calculations | Browser only | None | UI may imply live state |
| `nginx-lua` | Small status/actions | Request lifetime | Fixed helpers/read-only files | Blocking Nginx worker, command injection |
| `controllerhf` | Native parameters/signals framework | Controller lifetime | RP API/FPGA through native code | ABI and loader coupling, single-module ownership |
| `python-service` | Scheduling, storage, protocols, analytics | systemd | RP API, SCPI, fixed helpers | Python/extension ABI, latency |
| `cpp-service` | Native API, lower overhead | systemd | RP API, mapped driver/helper | Memory safety, build/sysroot ABI |
| `hybrid` | Thin web adapter plus durable worker | systemd | Service-owned | Split-brain state, duplicate validation |

## Ownership boundaries

Assign exactly one writer for each mutable resource:

| Resource | Required owner |
| --- | --- |
| FPGA configuration | Deployment/FPGA workflow |
| Generator/acquisition control registers | One controller or service |
| DAC output override | One controller or service |
| Persistent profile state | One service |
| Durable event journal | One service |
| Dashboard display state | Browser, derived from board status |
| Nginx routes | Application configuration |

If two processes can write the same hardware block, add arbitration or reject the design. A lock file alone does not make hardware ownership safe across crashes unless recovery is defined.

## Rejection rules

Reject:

- browser timers for hour-long profiles or monitoring;
- Lua loops, sleeps, long subprocesses, acquisition drains, or unbounded file reads;
- native binaries copied from another OS release;
- Nginx `location` patterns exposing arbitrary actions;
- services listening on all interfaces without an explicit remote API requirement;
- direct browser claims of “running” before board confirmation;
- a second application that assumes it can coexist with the active FPGA/controller owner;
- SCPI polling described as continuous or lossless without measured coverage.

## Manifest contract

Keep one `redpitaya-project.json` at the application project root. Validate it against `../../schemas/redpitaya-project.schema.json`:

```json
{
  "schema_version": 1,
  "project": {
    "slug": "my_app",
    "title": "My App",
    "version": "0.1.0"
  },
  "target": {
    "os_release": "3.00-57",
    "model": "z10_125",
    "role": "instrument"
  },
  "application": {
    "enabled": true,
    "architecture": "lua-api",
    "authentication": "none",
    "slug": "my_app",
    "routes": [
      "/my_app/api/status",
      "/my_app/api/version",
      "/my_app/api/stop"
    ],
    "controller_binary": null,
    "service_name": null,
    "python_abi": "none",
    "service_port": null
  },
  "fpga": {
    "enabled": false,
    "project": "none",
    "artifact_name": null,
    "device_tree_name": null,
    "loader": "none",
    "register_contract": null,
    "version_register": null,
    "magic_value": null,
    "clock_hz": null
  },
  "deployment": {
    "app_root": "/opt/redpitaya/www/apps",
    "mountpoint": "/opt/redpitaya",
    "staging_root": "/tmp/redpitaya-deploy/{deployment_id}",
    "backup_root": "/root/redpitaya-backups/my_app/{deployment_id}",
    "nginx_service": "redpitaya_nginx.service",
    "restore_read_only": true,
    "allow_live_output_test": false
  },
  "safety": {
    "output_default": "disabled",
    "stop_action": "POST /my_app/api/stop",
    "physical_test_requires_authorization": true,
    "rollback_required": true,
    "maximum_output_volts": null
  }
}
```

The manifest declares intent; it does not prove compatibility. Probe the board/image and validate actual artifacts. Read ABI and loader facts from `../../profiles/*.json`; do not duplicate them in an ad-hoc manifest object.
