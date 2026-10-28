# Register and acquisition API compatibility

Separate custom FPGA register ABI from Red Pitaya’s official oscilloscope register/API ABI.

## Contents

1. Three compatibility layers
2. OS 2.x profile
3. OS 3.00-57 profile
4. Custom register strategy
5. Official acquisition integration
6. Source compatibility
7. Runtime capability probing
8. Migration tests

## 1. Three compatibility layers

Track independently:

1. **FPGA hardware ABI**: register offsets, fields, descriptor layout, interrupt behavior.
2. **Red Pitaya C API ABI**: symbols, function signatures and structures in `librp`.
3. **Application runtime ABI**: glibc, GCC C++ ABI, Python ABI and native controller linkage.

A package can match one layer and fail another. For example, Python code can run on OS 3 while reading a stale OS 2 FPGA register map.

Do not copy official acquisition offsets into a custom block unless the custom design intentionally preserves the complete behavior software expects.

## 2. OS 2.x profile

The verified OS 2.00-35 and supplied OS 2.07-48 targets form one application
and FPGA packaging family:

- Ubuntu 22.04 userspace;
- Python 3.10 / `cp310-armhf` for compiled extensions;
- GCC 11;
- glibc 2.35;
- older official acquisition API surface;
- no declared interrupt-wait API in the matched profile;
- no declared acquisition 16-bit mode in the matched profile;
- no declared trigger timestamps in the matched profile;
- FPGA artifact `fpga.bit.bin` plus `fpga.dtbo`.

Keep the exact release/build in evidence and probe its live loader interface.
That exact string is provenance and deployment gating, not a reason to design a
different register ABI for every OS 2.x point release.

Design software for polling/older API only if official API use is required. A custom FPGA can still implement interrupts/timestamps, but it needs its own matching driver/register contract/overlay rather than assuming official API support.

Do not run a binary built against OS 3’s newer glibc/API on OS 2.

## 3. OS 3.00-57 profile

From the supplied exact image profile:

- Ubuntu 24.04.4 userspace;
- Python 3.12 / `cp312-armhf`;
- GCC 13;
- glibc 2.39;
- official acquisition interrupt wait/status support;
- acquisition 16-bit mode support;
- trigger/global/start timestamp facilities;
- FPGA artifact `fpga.bin`.

The newer facilities can improve official acquisition workflows but do not automatically eliminate re-arm gaps. Continuous custom event monitoring still requires persistent hardware processing and accounting.

Do not assume OS 3 source compiles unchanged against OS 2 headers. Inspect exact headers for renamed functions and signature changes.

## 4. Custom register strategy

For custom logic:

- allocate a documented, non-overlapping AXI region;
- expose `MAGIC`, ABI and capabilities;
- generate bindings from the bundled register contract;
- make application software use generated constants;
- reject wrong magic/ABI before any write;
- expose optional features through capabilities, not OS guesses;
- keep counters/descriptor format independently versioned.

This allows the same application source to support both OS releases when its Linux dependencies permit, while the FPGA ABI stays explicit.

Recommended software open sequence:

```text
map only declared span
read MAGIC
read ABI_VERSION
read CAPABILITIES
compare ABI major
read STATUS
confirm output override disabled
then allow configuration writes
```

On mismatch, unmap and fail safe. Do not “try likely offsets.”

## 5. Official acquisition integration

If using stock `v0.94` acquisition:

1. identify exact project/register document matching the OS/repository;
2. keep stock register semantics for every API-used function;
3. preserve ADC buffer layout, trigger state and pointer behavior;
4. preserve calibration/scaling expectations;
5. preserve interrupt bits and timestamps for OS 3 if application uses them;
6. run the official API’s acquisition regression on the target OS;
7. add custom registers in a separate, non-overlapping block when possible.

If changing the stock acquisition block incompatibly, do not claim support for the stock Red Pitaya API. Provide a custom service/driver and update the shared manifest.

### OS 3 additions

When using OS 3 facilities, verify exact headers and register docs for:

- interrupt mask/status/clear semantics;
- per-channel trigger and fill events;
- global/trigger timestamps;
- buffer-start timestamps;
- 16-bit sample mode and buffer layout;
- diagnostic registers;
- calibration structure changes.

Do not code from this category list alone. Read the exact supplied image headers or matching source revision.

## 6. Source compatibility

Use compile-time adaptation only where behavior is equivalent:

```c
#if RP_OS_API_MAJOR >= 3
    /* exact OS 3 API call */
#else
    /* exact OS 2 equivalent or explicit unsupported result */
#endif
```

Keep wrappers narrow:

```text
acq_start
acq_wait_event
acq_read_samples
acq_get_timestamp
```

Return “unsupported” rather than emulate a hardware timestamp with wall-clock time.

Build native components separately in matching sysroots:

- OS 2: ARM hard-float, glibc 2.35-compatible, Python 3.10 if applicable;
- OS 3: ARM hard-float, glibc 2.39-compatible, Python 3.12 if applicable.

Do not distribute one `controllerhf.so` or compiled Python extension for both releases unless ABI inspection and on-target tests prove it.

## 7. Runtime capability probing

Probe both software and hardware:

```text
OS release/profile
available librp symbol/version
loaded FPGA magic/ABI/capabilities
expected artifact epoch
device/interrupt node
```

Base feature use on capabilities:

- hardware timestamp available;
- descriptor FIFO available;
- DMA available;
- interrupt available;
- signed peak/energy fields available;
- snapshot counters available.

Do not infer hardware capability solely from OS 3. A stock or different bitstream may be loaded.

Log one startup compatibility record:

```text
os_release
target_profile
application_build
librp/API version
fpga_magic
fpga_abi
capabilities
epoch
```

Refuse writes when any required value mismatches.

## 8. Migration tests

For each OS:

- shared project contract validates;
- native application starts and links;
- expected artifact loads through exact loader;
- FPGA magic/ABI/capabilities read correctly;
- stock API tests pass if compatibility is claimed;
- trigger behavior and sample scaling match;
- timestamps are monotonic and units correct;
- interrupt mask/status/ack works where supported;
- 16-bit mode layout matches API where used;
- custom FIFO/counters survive software restarts;
- output is disabled after app/FPGA reset;
- wrong OS artifact filename is rejected;
- OS 3 binary is not accepted as an OS 2 runtime artifact without rebuild.

Maintain separate test result rows for OS 2.07-48 and OS 3.00-57. “Tested on Red Pitaya” is not sufficient.
