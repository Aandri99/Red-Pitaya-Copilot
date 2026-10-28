# Persistent Python and C++ services

## Contents

1. Unit design
2. Paths and permissions
3. State machine
4. Journaling
5. Updates and recovery
6. Tests

## Unit design

Use a dedicated systemd unit installed by `$deploy-red-pitaya-project`, not by the app build itself.

Baseline:

```ini
[Unit]
Description=Red Pitaya my_app service
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/redpitaya/www/apps/my_app/service
ExecStart=/usr/bin/python3 /opt/redpitaya/www/apps/my_app/service/app_service.py --host 127.0.0.1 --port 9187
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
CapabilityBoundingSet=CAP_SYS_RAWIO
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RuntimeDirectory=redpitaya-my_app
StateDirectory=redpitaya-my_app

[Install]
WantedBy=multi-user.target
```

The generated unit deliberately carries
`SCAFFOLD_PRIVILEGE_REVIEW_REQUIRED`. Confirm every directive on the exact
target systemd release. Retain `root` and `CAP_SYS_RAWIO` only when the reviewed
integration truly needs direct register access. Otherwise replace them with a
dedicated unprivileged user and an empty capability set. A warning-free
deployment-ready handoff requires this target-specific decision. Hardening that
blocks necessary hardware access must be adjusted narrowly, not removed
wholesale.

Do not use `Type=forking`, shell wrappers, background `&`, or infinite restart without backoff.

## Paths and permissions

- Static app: `/opt/redpitaya/www/apps/<slug>`.
- Runtime: `/run/redpitaya-<slug>` via `RuntimeDirectory`.
- Durable state: target-verified writable path, commonly managed by `StateDirectory`.
- Logs: stdout/stderr to journal unless a bounded application log is required.

Do not write changing state into the read-only app partition. Do not assume `/root` is the right operational data store.

## State machine

Persist only what is needed to recover safely:

- configuration version;
- monitoring/session ID;
- last durable cursor/counter;
- profile progress if restart is supported;
- safe-state evidence;
- last clean shutdown marker.

On startup, distinguish clean restart from crash. Resume only operations explicitly designed to be resumable; otherwise enter safe idle and report interrupted work.

## Journaling

For event records:

1. assign epoch and monotonic sequence;
2. write record;
3. flush according to durability requirements;
4. advance committed cursor atomically;
5. then acknowledge/publish.

Bound storage by size/time and expose free space. Never delete unacknowledged data silently.

For condition dashboards, distinguish authoritative totals from display histories. A counter reset does not automatically erase external cloud history; graph reset requires a separate cloud/data-retention operation.

## Updates and recovery

- Stop or quiesce the service before replacing executable code.
- Preserve compatible state or migrate it explicitly.
- keep the prior artifact and unit for rollback;
- run config/syntax checks before restart;
- verify readiness and safe state after restart.

Never automatically restart into an energized output state unless that behavior is a documented, authorized safety requirement.

## Tests

- clean start and stop;
- duplicate start;
- SIGTERM during startup/running/write;
- service crash and restart;
- invalid/corrupt state;
- storage full;
- upstream/hardware unavailable;
- browser/network disconnect;
- clock adjustment;
- sequence/counter rollover;
- OS reboot;
- upgrade and rollback.

## Contain physical MMIO faults

Mapping `/dev/mem` may succeed even when the loaded FPGA has no responding AXI
slave at the address. The first load/store can terminate the reader with
SIGBUS, which same-process Python exception handling cannot reliably contain.

For service-backed hardware apps:

1. keep the HTTP/WebSocket process free of `/dev/mem` mappings;
2. let one supervised worker process own the mapping;
3. expose only reviewed offsets through a bounded IPC protocol;
4. convert worker signal death, EOF, malformed response, or timeout to
   `hardware_unavailable`;
5. block all writes until read-only magic, ABI, capabilities, and build ID
   match exactly;
6. close or terminate the worker on shutdown and on server-bind failure.

Do not restart a worker in a tight loop against a known-bad FPGA. Keep
simulation separate and label it as non-hardware evidence.

## Restart-safe loopback listeners

Nginx-proxied services restarted while WebSockets are closing may encounter
`TIME_WAIT`. Permit safe address reuse on the loopback-only listener, close
runtime resources if bind fails, and test an immediate stop/start. Acceptance
requires an actual listener plus a successful proxied API response within a
short bounded readiness deadline. An active systemd unit alone does not prove
readiness and may be reported before application initialization/socket bind.
