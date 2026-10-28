# Safe FPGA hardware testing

Prepare staged, reversible tests. Loading an FPGA image can immediately change high-speed outputs and expansion pins.

## Contents

1. Authorization boundary
2. Preflight
3. Physical setup
4. Load and identity
5. Staged functional tests
6. Continuous-acquisition stress
7. Stop and rollback
8. Evidence and failure handling

## 1. Authorization boundary

This FPGA skill may:

- inspect local files and offline images;
- build/simulate/package when tools are available;
- inspect a live board read-only when the user placed it in scope;
- write a hardware test plan.

It must not, without explicit user authorization:

- load a bitstream/overlay;
- write `/dev/mem` or `monitor` registers;
- start DAC/output override;
- remount `/opt/redpitaya`;
- replace files on a board;
- stop services;
- reflash an SD card.

Use `$deploy-red-pitaya-project` for transactional live changes.

## 2. Preflight

Before any authorized load:

1. identify the exact board by profile and second corroborating evidence;
2. match `target.model`, Vivado `MODEL` and FPGA part;
3. match `target.os_release` and artifact name;
4. validate `redpitaya-project.json`;
5. validate the FPGA package;
6. confirm simulation passes;
7. confirm implementation timing and critical-warning review;
8. record currently loaded FPGA/project;
9. copy known-good bitstream/overlay and loader command;
10. record network recovery path;
11. confirm safe stop action;
12. ensure user authorized the exact board and physical test scope.
13. prove how the loaded image will be bound to the qualified artifact; ABI
    magic alone is insufficient;
14. inventory every process, web API, root helper and service able to write the
    same register page, and establish one enforceable writer lease.

Do not proceed on an identity or timing mismatch.

## 3. Physical setup

Assume pins may glitch during reconfiguration.

- Disconnect sensitive DUTs.
- Terminate/attenuate high-speed outputs appropriately.
- Verify common ground and cable direction.
- Use an oscilloscope/DMM appropriate for expected signals.
- Select correct probe attenuation/impedance.
- Verify Red Pitaya input range and coupling.
- Avoid direct OUT-to-IN loops without suitable attenuation/termination and voltage analysis.
- Disconnect expansion-pin loads unless their reset behavior is known.
- Keep power and Ethernet physically accessible.
- Have an SD-card recovery route for development boards.

For a generator:

- output override must reset low;
- DAC sample data must reset benign;
- stock DAC path or zero output must be selected on custom disable;
- software stop must not depend on the browser.

For an acquisition-only design:

- ensure no custom output or direction enable is accidentally asserted;
- verify ADC inputs stay inside board specifications;
- start with an internal or attenuated signal.

## 4. Load and identity

Authorized procedure:

1. stop/disable application output through its supported command;
2. verify output physically;
3. stage files outside persistent app storage;
4. verify SHA-256 on target;
5. invoke the exact image’s loader with matching bitstream and overlay;
6. capture loader output and kernel messages;
7. verify FPGA Manager reports operating;
8. do read-only magic/ABI/capability reads;
9. read a unique build identity and bind it to the qualified package hash;
10. compare each value to the project manifest/register contract and release
    receipt;
11. abort before any write on mismatch or when only generic ABI magic is
    available.

Do not attempt likely alternative base addresses when magic is wrong. Reload known-good hardware.

After identity, read:

- status;
- output override;
- error sticky;
- FIFO level;
- epoch;
- counters.

Expected initial state:

```text
ready as designed
output override disabled
generator/output disabled
FIFO empty or explicitly documented
error flags clear
new epoch visible
```

## 5. Staged functional tests

### Stage A: register-only

- read identity repeatedly;
- verify invalid/reserved reads;
- write harmless configuration while disabled;
- read it back;
- issue snapshot;
- clear a synthetic/sticky diagnostic if safely testable;
- confirm no physical output.

### Stage B: internal logic

- use simulation/test source or internal loopback;
- exercise detector/queue without physical DAC;
- verify event, sequence, FIFO and timestamps;
- compare counts to known stimulus.

### Stage C: low-amplitude measured output

Only if authorized:

- set minimum safe configuration;
- verify output remains disabled during config;
- enable under scope measurement;
- confirm amplitude, offset, frequency and polarity;
- disable immediately;
- verify physical safe state.

Do not jump to full-scale or long autonomous profiles.

### Stage D: nominal behavior

- increase one parameter at a time;
- record measured versus requested values;
- test both channels only when wiring is verified;
- verify stop under active output;
- verify application/network loss returns or leaves hardware in the documented safe policy.

## 6. Continuous-acquisition stress

Use a known event source/count:

1. reset/start a new monitoring epoch;
2. record initial snapshot;
3. inject exactly N slow events;
4. verify accepted count and N descriptors;
5. increase to nominal rate;
6. stop Linux drain temporarily;
7. fill FIFO;
8. exceed capacity by a known amount;
9. resume drain;
10. snapshot event/drop/queued totals coherently;
11. verify accounting invariant;
12. run long enough to test timestamp/counter high words;
13. restart the software while FPGA remains active;
14. verify FPGA totals continue;
15. reload FPGA and verify epoch change is detected.

Also simulate/measure:

- network outage;
- dashboard closed;
- service restart;
- consumer CPU load;
- interrupt storm prevention;
- FIFO watermark extremes;
- reset during an event.

Do not use dashboard-published totals as authoritative when testing hardware counts.

## 7. Stop and rollback

Safe stop order:

1. clear custom output override;
2. disable generator/output enable;
3. verify physical output;
4. disable event/DMA acceptance if needed;
5. mask interrupts;
6. stop software consumer;
7. save final coherent counters/epoch;
8. load known-good bitstream/overlay;
9. verify known-good magic/function;
10. restore service/app state.

For a muxed DAC design, clearing the custom override proves only that the
custom source lost ownership. It may return the connector to a stock
ASG/PID/digital-loop source that is still nonzero. Name the verified state
precisely (for example, `custom override disabled`) and prove electrical quiet
separately by owning/zeroing the fallback source, measuring the connector, or
using an independent relay/clamp/disconnect.

An application mutex or `flock` is not exclusive ownership when legacy Lua
endpoints, root helpers, other services, or direct `/dev/mem` tools can bypass
it. Quarantine those writers or redesign all of them around one enforced
hardware lease before a physical-output test.

If custom software is unresponsive, use the pre-established independent hardware/register stop path. A UI button alone is not a safety mechanism.

Rollback must identify:

- known-good files and hashes;
- exact loader command;
- expected model/OS;
- expected post-rollback FPGA state;
- how to recover when network access is lost.

## 8. Evidence and failure handling

Capture:

- board identity and OS;
- package hashes;
- authorization scope;
- physical wiring/attenuation;
- loader result;
- manager/kernel status;
- magic/ABI/capabilities;
- every configuration write;
- measured output;
- test counts and counter snapshots;
- stop/rollback result.

Failure actions:

| Failure | Immediate action |
|---|---|
| Wrong magic/ABI | no writes; reload known-good |
| FPGA Manager not operating | capture logs; rollback |
| Unexpected physical output | disconnect/disable; rollback |
| Timing/clock not ready | keep output disabled; rollback |
| Interrupt storm | mask source; stop consumer; rollback if needed |
| FIFO loss unexplained | preserve counters/logs; do not reset; diagnose |
| Network lost | use established local/recovery path |
| Kernel fault/hang | power-safe recovery and known-good SD/image |

Never repeatedly load or poke registers to “see if it starts working.” Preserve failure evidence first.
