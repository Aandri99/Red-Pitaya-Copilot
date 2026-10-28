# Application lifecycle and ownership

## Contents

1. States
2. Startup
3. Launcher ownership and hardware persistence
4. Browser lifecycle
5. Shutdown
6. Fault recovery
7. Resource ownership

## States

Use explicit states:

```text
unavailable
  -> starting
  -> ready_safe
  -> running
  -> stopping
  -> ready_safe
  -> fault_safe | fault_unknown
```

Do not collapse process reachable, hardware initialized, and output enabled into one “online” flag.

Report independent state axes:

- `service`: reachable/startup/running/faulted;
- `access`: mode, allowed and exact blocked reason;
- `api_compatible`: expected versus observed contract;
- `hardware_ready`: confirmed/false/unknown with reason;
- `acquisition`: not requested, filling, completed, empty or failed;
- `source`: configured preview, simulation, measured ADC or none;
- `physical_output`: disabled, enabled, unknown or authorization required.

A missing token is an access state. An unknown FPGA is a hardware state. “No
samples” is an acquisition state. Never display one as the explanation for
another, and never collapse these axes into one generic `locked` flag.

Include a monotonically changing `service_epoch` or startup timestamp so clients detect restarts. Include `state_revision` or command ID so out-of-order responses cannot overwrite newer UI state.

## Startup

1. Parse immutable configuration.
2. Verify OS/model/API/FPGA contract.
3. Acquire exclusive ownership.
4. establish safe hardware state;
5. restore only explicitly recoverable durable state;
6. start workers;
7. publish readiness.

On any failure, unwind in reverse order and report `fault_safe` only if safe state was confirmed.

## Launcher ownership and hardware persistence

A direct `/<slug>/` route, a visible tile and stable hardware ownership are
three different acceptance facts. On launcher-managed OS images, Bazaar may
unload the active app or reload an overlay when a lifecycle controller is
missing, incompatible or unexpectedly exits. That can erase generator and
acquisition state even while the browser initially appears to work.

For an instrument that must preserve output or acquisition state:

1. identify the exact component that owns the launcher lifecycle;
2. identify every app-switch, start, stop and page action that can load an FPGA;
3. keep a presentation-only `controllerhf.so` passive when a supervised
   service owns hardware;
4. snapshot overlay/build identity and relevant hardware state before and
   after launcher start, app switching and repeated status/capture calls;
5. observe required continuous behavior for at least ten seconds; and
6. prove exactly one launcher tile when stock-home-screen visibility is part
   of the requirement.

Do not repair a lifecycle failure with a browser timer or repeated Start calls.
Find the owner/unload/reset boundary and fix it in a new validated package.

## Browser lifecycle

The browser is disposable:

- multiple tabs may exist;
- requests may be duplicated;
- responses may arrive out of order;
- network may fail after the board accepted a command;
- page refresh must not reset hardware or counters;
- closing the page must not be relied upon for safe stop.

Use command IDs/idempotency where mutations may be retried. Re-fetch authoritative status after reconnect.

Status, version, health and read-only capture calls must be side-effect free.
Do not let a per-request hardware-library initializer reset unrelated blocks.
If the exact target API provides a verified attach-without-reset mode, use it;
otherwise keep hardware initialized in the single owning service or report the
operation unavailable. Never silently fall back from a non-reset attach to a
known resetful initializer merely to return status.

## Shutdown

1. Stop accepting new start/config mutations.
2. Mark state `stopping`.
3. cancel workers;
4. disable/return outputs to safe path;
5. flush bounded durable state;
6. release hardware ownership;
7. terminate.

Set a bounded timeout and define escalation if a worker cannot stop. Do not force-kill before attempting safe output disable when hardware control remains responsive.

## Fault recovery

Classify:

- transient communication fault;
- invalid configuration;
- hardware/API mismatch;
- worker crash;
- storage full/corrupt;
- upstream unavailable;
- safe-state confirmation failed.

Retry only bounded, known-transient faults. Latch unsafe/unknown hardware faults until an operator acknowledges and status is reverified.

## Resource ownership

Use one owner for:

- FPGA programming;
- each hardware peripheral/register block;
- DAC override;
- acquisition buffer;
- durable journal;
- profile scheduler.

Expose owner identity in diagnostics. Release locks/mappings on graceful exit, but design startup to recover from stale markers after crashes. Never assume a PID file alone proves the owner is alive or matches the process.
