# Generator and acquisition instrument patterns

Use this reference for arbitrary generators, PD simulators, loopback tools and
other applications that generate on OUT1/OUT2 while observing IN1/IN2. It is a
design pattern, not proof of compatibility with an unprobed board or Red Pitaya
API build.

## Contents

1. Map the complete signal path
2. Choose ownership and lifecycle
3. Preserve hardware during read-only calls
4. Generate from one canonical waveform
5. Capture calibrated input without disturbing output
6. Support a remote acquisition board
7. Render truthful waveforms
8. Accept the installed instrument

## 1. Map the complete signal path

Before editing, map:

```text
browser -> Nginx/OpenResty -> validated API -> hardware owner -> Red Pitaya API
        -> FPGA generator -> DAC/OUT -> physical cable/load -> ADC/IN
        -> FPGA acquisition -> hardware owner -> JSON/WebSocket -> browser
```

Record every operation that can load an overlay, initialize/release the Red
Pitaya API, reset acquisition, clear counters, disable an ASG channel or switch
launcher ownership. A diagram that ends at a JSON response is incomplete.

## 2. Choose ownership and lifecycle

Use exactly one writer for generator state and exactly one owner for each
acquisition buffer.

- A persistent Python/C++ service is preferred when output, profiles, capture
  or state must survive browser closure.
- Nginx/Lua may validate and forward bounded requests; it must not run long
  acquisition loops or become a second hardware owner.
- A `controllerhf.so` added for stock-launcher discovery is lifecycle-only when
  the service owns hardware. It must not initialize `librp`, load an FPGA,
  start/stop output or duplicate service state.
- A launcher start script must not hide an unload/reload loop with retries.
  Bounded retry is acceptable only for a documented transient after the real
  lifecycle contract is sound.

Treat these as separate acceptance facts:

1. the direct route works;
2. `/bazaar?apps=` contains the intended slug exactly once;
3. the tile opens the intended route;
4. switching apps does not orphan or duplicate the hardware owner; and
5. the required signal persists without an unexpected overlay reload.

Keep backups outside every launcher-scanned app root. A tile-less helper must
omit `info/info.json` deliberately and must be documented as a service rather
than a hidden second application.

## 3. Preserve hardware during read-only calls

Status, version, health and read-only capture must not change generator state.
Do not call a resetful hardware initializer once per request.

On some verified OS 2.x API builds, `rp_InitReset(false)` attaches without a
global reset. This name is not a cross-version guarantee. Inspect the exact
headers/library and test the exact target. When a non-reset attach is
unavailable, use the already initialized single-owner service. Do not silently
fall back to `rp_Init()` from a read-only endpoint if that call resets hardware.

Prove the invariant by comparing before/after snapshots of:

- overlay/build/register-map identity;
- ASG channel enable, waveform/mode, frequency, amplitude and offset;
- unrelated output channel state;
- acquisition owner and epoch;
- authoritative hardware counters; and
- service epoch/restart count.

Repeat status and capture while observing the physical loopback for at least
ten seconds. If output disappears, trace launcher unload, overlay selection,
API initialization/release and ASG registers before changing waveform timing.

## 4. Generate from one canonical waveform

Create one normalized sample array and use it for both browser preview and
hardware upload. Keep physical amplitude and offset as separate bounded
settings. For noise, retain a seed/frozen sample set so the preview represents
the transmitted data.

Before enabling output:

1. validate channel, units, sample count, frequency, amplitude and offset;
2. acquire the generator writer lock;
3. verify exact OS/model/API/FPGA identity;
4. disable the selected output;
5. load the bounded waveform/configuration;
6. read back critical configuration;
7. select the reviewed trigger and continuous/burst mode;
8. enable physical output last; and
9. confirm the enabled state independently.

Use BRAM, deep-memory generation or a custom FPGA path only when the exact
target capability and buffer contract are verified. Do not infer a universal
sample threshold from one OS image. Stop remains idempotent and dominant over
an in-flight Apply/Start.

## 5. Capture calibrated input without disturbing output

A same-board capture operation owns only the selected acquisition resources.
It must not reset the generator, reload the overlay or alter another channel.

A robust bounded capture normally:

1. snapshots the acquisition settings it promises to restore;
2. stops/resets only the selected acquisition engine;
3. applies the physical LV/HV range that matches the actual jumper/front end;
4. enables calibration when the exact API supports it;
5. selects decimation from the requested physical time window;
6. starts acquisition and uses a reviewed trigger (immediate is appropriate
   when DC/flat signals must remain visible);
7. waits to a bounded deadline;
8. reads calibrated volts and timing metadata;
9. restores the promised acquisition state and verifies it; and
10. reports primary and restoration failures separately.

For ADC rate `f_adc`, requested window `T` and `N` returned samples, an initial
decimation estimate is:

```text
ceil(T * f_adc / (N - 1))
```

Clamp or quantize it to the exact target's supported values, then return the
actual sample period, actual window and chosen decimation. Useful frame fields
include samples, sample count, Vpp, RMS, mean, minimum, maximum, trigger source,
range/calibration identity, capture epoch and timeout/restoration state.

The voltage display scale is not ADC gain. Fixed/auto graph limits must not be
presented as changing the physical input range.

## 6. Support a remote acquisition board

For two boards, deploy and verify the acquisition consumer before enabling the
generator. Prefer the acquisition board's same-origin app route or a narrowly
scoped tile-less read-only service. Bind internal services to loopback unless
cross-board/browser access was explicitly designed.

If browser cross-origin capture is required:

- allow only reviewed origins and methods;
- keep mutation endpoints out of wildcard CORS;
- return a version/capability header or JSON contract;
- distinguish DNS, CORS, HTTP, API-version and empty-capture failures; and
- label the exact acquisition host and measured source in the UI.

Do not use a remote hostname as board identity. Deployment and qualification
still require independent identity and role evidence.

## 7. Render truthful waveforms

Label each trace as one of:

- configured/reconstructed preview, never measured;
- simulated evidence;
- same-board measured ADC;
- remote-board measured ADC; or
- unavailable/empty with the exact reason.

For dense frames, preserve spikes with a min/max envelope per horizontal pixel
instead of selecting every nth point. Show actual time and voltage units from
returned metadata. Use bounded polling, abort stale requests, discard responses
from an older service/capture epoch and back off after failures.

## 8. Accept the installed instrument

Run in increasing risk order:

1. exact route, version, hashes and effective Nginx/service configuration;
2. exactly one launcher tile when required and no helper/backup tiles;
3. stable lifecycle/service ownership through app switching;
4. status/capture non-mutation snapshots;
5. idempotent Stop with independently confirmed output disable;
6. low-risk authorized physical loopback;
7. continuous signal for at least ten seconds with repeated status/capture;
8. frequency/amplitude changes reflected in measured timing/Vpp;
9. bounded timeout, disconnect and recovery behavior; and
10. final confirmed disabled state.

If a custom FPGA is optional, keep a qualified stock fallback. Route builds and
timing/DRC review to `$build-red-pitaya-fpga`; quarantine every bitstream whose
routed timing, target part, constraints or recovery path is unresolved. Route
all live installation and rollback to `$deploy-red-pitaya-project`.
