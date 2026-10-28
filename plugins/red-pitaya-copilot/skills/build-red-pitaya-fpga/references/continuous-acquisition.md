# Continuous acquisition, FIFO, DMA and loss accounting

Use hardware-continuous processing when events must not disappear during software re-arm, network delay or dashboard updates.

## Contents

1. Why triggered polling loses coverage
2. Reference architecture
3. Event detector contract
4. Descriptor design
5. FIFO sizing and overflow
6. Authoritative counters
7. Timestamp and epoch
8. Interrupt strategy
9. DMA option
10. Acceptance tests

## 1. Why triggered polling loses coverage

A conventional software loop:

```text
arm acquisition -> wait for trigger -> fill buffer -> transfer samples ->
analyze -> publish -> arm again
```

cannot observe events during transfer, analysis, network delay or reconfiguration. Faster software reduces but does not eliminate this blind interval.

Do not claim zero loss from:

- repeated SCPI captures;
- repeated API arm/wait/read calls;
- a browser polling loop;
- a Linux thread with a shorter sleep;
- increasing only the oscilloscope buffer.

Continuous coverage requires the event acceptance path to remain active in FPGA logic independently of software.

## 2. Reference architecture

Use:

```text
ADC stream
  -> normalization / optional filter
  -> event candidate detector
  -> qualification + retrigger policy
  -> event accepted
       -> authoritative counters
       -> descriptor builder
       -> descriptor FIFO
       -> optional sample-window buffer / DMA
       -> optional interrupt
  -> Linux drains descriptors
  -> application publishes derived metrics
```

Keep detection/counter progression active while software is idle. Software drains and interprets; it does not define whether an event existed.

For waveform evidence, choose one:

- descriptor only: timestamp, peak, energy, duration, polarity;
- fixed pre/post sample window copied to BRAM;
- DDR ring buffer with descriptor pointers;
- selective DMA of windows.

Do not stream every 125 MS/s sample through AXI-Lite.

## 3. Event detector contract

Define before RTL:

- input channel and signed sample width;
- sample frequency;
- baseline removal/filter;
- positive/negative/absolute threshold;
- threshold comparison equality;
- hysteresis;
- minimum consecutive samples;
- end-of-event rule;
- dead time/refractory period;
- retrigger/merge rule;
- peak and energy accumulation;
- maximum event length;
- behavior for an event crossing timestamp wrap/reset.

Example state machine:

```text
IDLE
  candidate threshold crossed -> QUALIFY
QUALIFY
  minimum width reached -> ACTIVE
  signal drops early -> IDLE
ACTIVE
  update peak/energy/duration
  end condition -> EMIT
EMIT
  increment event counter and attempt descriptor enqueue -> DEAD_TIME
DEAD_TIME
  timer expires -> IDLE
```

If an event can be accepted while FIFO is full, increment event and drop totals together according to policy. Do not block the detector unless backpressure is explicitly acceptable.

## 4. Descriptor design

Use fixed-width descriptors with a versioned layout. A useful baseline:

| Field | Suggested width | Meaning |
|---|---:|---|
| `epoch` | 32 | monitoring generation |
| `sequence` | 64 | accepted-event sequence |
| `timestamp` | 64 | first/peak sample clock |
| `peak_signed` | 16 or ADC width | signed maximum magnitude sample |
| `duration_samples` | 32 | qualified event length |
| `energy` | 64 | sum/saturated metric |
| `flags` | 16/32 | polarity, clipping, merge, data loss |
| `sample_pointer` | 32/64 | optional waveform window |

Specify packing order and endianness in the register contract/descriptor document. Keep descriptor width aligned to the FIFO/software transfer word.

Include a descriptor-format version in capabilities or each stream header. Never change field meaning without an ABI-major change.

### Sequence semantics

Choose and document:

- increment for every accepted event, whether queued or dropped; or
- increment only for emitted descriptors while a separate event/drop total reveals loss.

The first choice makes sequence gaps directly expose FIFO loss and is preferred for predictive monitoring.

## 5. FIFO sizing and overflow

Calculate, do not guess.

Let:

```text
R_peak = maximum accepted event rate [events/s]
T_stall = maximum expected software/DMA stall [s]
M = safety multiplier
N_required = ceil(R_peak * T_stall * M)
```

Round to an implementation-friendly depth and verify BRAM use. Also test a burst exceeding the design assumption.

FIFO policies:

- **drop newest**: preserve queued chronology; increment drop count for each rejected descriptor;
- **drop oldest**: retain freshest state but requires explicit overwritten count;
- **backpressure detector**: only valid when missed physical events are acceptable and counted;
- **spill to DDR**: higher complexity, longer coverage.

Expose:

- current level;
- programmable interrupt watermark;
- high-water mark;
- full/overflow sticky flag;
- total dropped descriptors.

Clearing FIFO must not clear lifetime event count unless an explicit epoch reset requests both.

## 6. Authoritative counters

Maintain in FPGA:

- total accepted events;
- total descriptor drops;
- total clipped/saturated events;
- FIFO high-water;
- optional samples observed;
- optional detector-disabled sample clocks;
- optional DMA errors.

Use 64-bit totals for long operation. Snapshot them coherently.

### Coverage accounting

Expose enough to distinguish “zero events” from “not observing”:

```text
monitoring enabled clock count
total sample clock count or epoch start timestamp
detector disabled clock count
FPGA epoch
service/drain health (software)
```

Coverage can be reported as:

```text
enabled_sample_clocks / elapsed_sample_clocks
```

but define exclusions for FPGA reload and clock loss. Do not display 100% coverage when the epoch restarted unnoticed.

## 7. Timestamp and epoch

Use a free-running sample-clock timestamp. Document:

- clock frequency;
- counter width;
- reset source;
- wrap interval;
- whether timestamp represents event start, peak or end;
- relation to wall clock.

Wall-clock mapping belongs in software:

```text
wall_time ~= anchor_wall_time +
             (event_sample_timestamp - anchor_sample_timestamp) / sample_hz
```

Refresh anchors and detect FPGA epoch changes. Network time does not belong in a high-speed detector data path.

An explicit monitoring reset should:

1. disable acceptance or create an atomic boundary;
2. increment/change epoch;
3. reset selected per-epoch counters;
4. clear descriptor FIFO if requested;
5. reset sequence to documented start;
6. capture new timestamp anchor;
7. resume.

Lifetime persistence across FPGA reload requires software/nonvolatile storage; FPGA totals cannot survive reconfiguration.

## 8. Interrupt strategy

Do not interrupt the CPU for every high-rate event. Prefer:

- FIFO watermark;
- transition empty -> nonempty;
- DMA completion;
- overflow/error.

Use interrupt coalescing or a watermark plus periodic drain. Ensure the interrupt source has:

- mask;
- live status;
- sticky pending status;
- acknowledge/clear semantics;
- race-free recheck after drain.

Level interrupts are often safer for FIFOs: keep asserted while level is above condition. Software drains, acknowledges, then rechecks before sleeping.

OS 3.00-57 exposes more official acquisition interrupt/timestamp facilities than OS 2.07-48, but custom logic still needs a matching device-tree interrupt path and software handler. Do not confuse official oscilloscope interrupts with a custom IP interrupt.

## 9. DMA option

Use DMA when descriptor/sample throughput exceeds efficient AXI-Lite polling.

Architecture choices:

- AXI DMA simple mode for discrete transfers;
- scatter-gather DMA for descriptor rings;
- custom AXI master writing a reserved DDR ring;
- existing `stream_app` data path where compatible.

Specify:

- physical/reserved memory region;
- cache coherency policy;
- descriptor ownership bits;
- producer/consumer indexes;
- alignment and burst length;
- wrap behavior;
- memory barriers;
- interrupt/coalescing;
- overrun behavior;
- maximum transfer size.

Never let FPGA write arbitrary physical addresses supplied by unvalidated userspace. Restrict/program bounds and disable DMA on reset.

If using reserved memory, add it to the matching device tree and ensure Linux does not allocate it. If using a kernel driver, define the DMA API and cache mapping there rather than ad-hoc `/dev/mem`.

### Ring invariants

- producer never overtakes consumer without incrementing overrun;
- ownership transfers only after payload is complete;
- software applies read barrier before payload access;
- hardware applies write ordering before ownership flag;
- epoch invalidates stale descriptors after reload/reset.

## 10. Acceptance tests

Simulation:

- event at every detector boundary;
- bursts below/at/above FIFO capacity;
- slow/random consumer;
- simultaneous event and clear/snapshot;
- timestamp wrap;
- sequence and drop equations;
- DMA ring wrap and consumer stall;
- reset/reload epoch.

Hardware, after authorization:

1. inject a counted synthetic digital/event stream;
2. compare injected count to authoritative event total;
3. stop Linux drain until FIFO fills;
4. verify high-water/full/drop behavior;
5. resume and verify descriptor sequence gaps equal loss policy;
6. sustain maximum expected rate;
7. measure service latency distribution;
8. test clock/reset interruption;
9. reload and verify epoch change;
10. confirm output remains disabled throughout acquisition tests.

Required accounting invariant:

```text
accepted_events =
  retained/emitted_descriptors
  + dropped_descriptors
  + descriptors_currently_queued
```

Adjust for descriptors drained since the comparison snapshot. Validate all terms from one coherent snapshot when possible.
