# Measurement and reporting method

Freeze algorithms and thresholds before the run. Preserve unmodified raw
samples and configuration metadata.

## Provenance labels

- `measured`: ADC samples, register/status readback and hardware counters.
- `derived`: calculations whose inputs are measured.
- `reconstructed`: UI preview or waveform generated from settings.

Only measured phase evidence satisfies the reporter.

The pre-output FPGA build-ID readback and writer-inventory result are also
`measured`. A configured build name, UI label, ABI magic or copied release
receipt is not a measured loaded-build identity.

## Baseline

Capture baseline with OUT1 disabled before any output command. Record sample
rate, count, trigger state, IN1 range/coupling/termination, offset, RMS/noise,
peak, clipping and sequence/data gaps. Do not substitute a baseline captured
after the event.

## Candidate accounting

Use coherent generator event-counter snapshots:

```text
emitted candidates = counter_after - counter_before
detected candidates = count from the frozen IN1 detector method
detection fraction = detected / emitted
candidate loss fraction = max(0, emitted - detected) / emitted
```

The one-shot plan expects exactly one emitted candidate. Treat zero emitted as
failure; do not divide by zero.

These are bounded candidate counts. They do not prove:

- continuous capture between records;
- zero acquisition dead time;
- absence of FPGA FIFO drops;
- equivalence to calibrated partial-discharge charge.

Do not state "zero dead time" unless a separately engineered continuous
acquisition architecture and evidence prove it. This skill always records
`zero_dead_time_claimed: false`.

## Voltage and headroom

Use Vpeak consistently. The planner calculates the largest credible IN1 peak
from the lowest credible attenuation. During the run, record the measured
absolute peak and over-range flag. Stop on clipping or a range violation.

Do not compare generator percentage/DAC codes directly with IN1 volts.

## Timing

Use monotonic/hardware timing for control decisions. UTC timestamps document
evidence ordering and authorization windows. Phase timestamps must be ordered,
non-overlapping and contained within run start/completion.

The one-shot must begin only after the baseline phase ends and after watchdog
readiness. It must finish inside the plan's positive, at-most-five-second
window.

## Writer and watchdog

Record the plan-bound writer ID during the one-shot. Any other owner is a
structural error. Record the independent cutoff as ready during output.

An application exception handler is cleanup, not an independent watchdog.

Before arming output, re-read the exact plan-bound FPGA build ID and repeat the
five-category writer inventory. Record the build ID and `exclusive` result in
phase `verify-loaded-fpga-and-writers`. Any drift or bypass writer is a
structural rejection. A cooperative lock proves only cooperation among its
participants; it says nothing about an API, root helper, service or
`/dev/mem` tool that ignores it.

The bound release must include independent `passed` timing, constraints and
DRC evidence. Treat `blocked`, `failed`, `incomplete`, missing or unknown as
not released.

## Restoration

The restore artifact is measured evidence. Compare the complete versioned IN1
state object with the bound pre-state using exact semantic JSON equality.

A mismatch yields `FAIL`; it must not be hidden by normalizing to defaults.

## Report outcomes

`build_single_board_report.py` emits:

- evidence JSON with plan/observation bindings and an evidence-core SHA-256;
- criteria CSV;
- Markdown operator report.

`PASS` requires:

- every planned phase passed;
- final output disabled;
- exact IN1 restoration in active mode;
- valid single-use run record within authorization time;
- a unique loaded FPGA build ID bound to the qualified package, exact artifact,
  release receipt, register contract and independent release review;
- `passed` timing, constraint-completeness and DRC reports;
- complete writer inventory with exactly one plan-lock participant, no bypass
  writer and a matching pre-output `exclusive` re-read;
- expected emitted candidate count;
- detection and loss fractions within frozen acceptance limits;
- measured IN1 peak within limit;
- no required-overrange violation.

`FAIL` is a valid report when structurally trustworthy evidence misses one or
more criteria.

A tool error means the package cannot be trusted: examples include plan/input
tampering, identity drift, replayed nonce, output outside the allowed phase,
output before baseline, invalid phase order, loaded FPGA build drift,
package/artifact/receipt tampering, failed release status, incomplete writer
inventory, bypass writer, writer/watchdog mismatch, unverifiable file binding
or a zero-dead-time claim.

The report certifies only the exact board, cable, range, loaded FPGA release,
driver contract, reviewed writer set, one-shot and time window in the plan. It
does not certify other builds, newly installed writer paths, other amplitudes,
long-term operation, another board or continuous acquisition.
