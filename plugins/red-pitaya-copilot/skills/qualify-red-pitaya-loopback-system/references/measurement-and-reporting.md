# Measurement and reporting method

Use the same documented algorithm for every run. Record algorithm names and
parameters in observation `method` fields. Do not tune an algorithm after
seeing a result unless a new plan/run is created.

## Contents

1. Evidence provenance
2. Sampling and timebase
3. Event accounting
4. Waveform pairing
5. Delay, gain and correlation
6. Polarity, width, energy and ringing
7. Phase
8. Dead time and data loss
9. Reset and rollover
10. Report interpretation

## 1. Evidence provenance

Use these labels exactly:

- **measured**: digitized ADC samples, hardware status readback or authoritative
  counter snapshot;
- **derived**: calculation whose inputs are measured;
- **reconstructed**: waveform or count generated from configured settings,
  model assumptions or a UI preview.

The generator preview is reconstructed even when it accurately reflects
settings. The local generator-board input and remote acquisition-board input
are measured.

Never graph a reconstructed preview as an oscilloscope capture. Never use its
displayed event count as the authoritative emitted total.

## 2. Sampling and timebase

Record for each measured channel:

- sample rate and units;
- sample count and pretrigger count;
- trigger mode and threshold;
- input range, coupling, termination and calibration identifier;
- hardware timestamp/counter source where available;
- sequence/epoch and FIFO metadata;
- UTC receipt time only as transport metadata.

Do not use host wall-clock arrival to claim microsecond inter-board delay.

### Unsynchronized Red Pitaya clocks

Two independent boards normally have independent ADC clocks. For remote delay
or phase:

1. use a shared trigger/reference if physically present and documented; or
2. estimate relative alignment from the same fanned-out waveform and report the
   method/uncertainty; or
3. report the value as unavailable/failing if the required uncertainty cannot
   be met.

Do not silently treat NTP synchronization as ADC-clock synchronization.

Convert all sample indices with the exact endpoint sample rate. Preserve raw
integer samples and calibration/scaling metadata when possible.

## 3. Event accounting

Take coherent counter snapshots immediately before and after each output phase.

Calculate:

```text
emitted = generator event-counter delta
local detected = local detector counter delta
remote detected = remote detector counter delta
```

Handle modular counters with their declared width:

```text
delta = (after - before) mod 2^width
```

If an epoch changes unexpectedly, do not combine deltas across the boundary.
Record the discontinuity and fail the integrity gate unless the exact reset
phase caused it.

Use hardware event counters when the approved contract advertises
`event-counter`. Software/UI trigger counts can be included as diagnostics but
must be labelled derived or non-authoritative.

Useful ratios:

```text
local detection fraction = local detected / emitted
remote detection fraction = remote detected / emitted
```

Define the zero-emitted case before the run. Never divide by zero or substitute
one event.

## 4. Waveform pairing

Pair local and remote records to the same emitted event using, in preference:

1. shared event ID;
2. shared hardware timestamp/reference;
3. deterministic sequence index plus counter snapshots;
4. waveform cross-correlation inside a bounded plan-defined search window.

Reject ambiguous pairs. Report the rejected/unpaired count as data loss or
pairing loss according to the acceptance contract.

Before feature extraction:

1. remove the output-disabled baseline offset using the pre-event window;
2. apply only the plan-approved calibration;
3. flag clipped samples;
4. do not filter unless filter definition is fixed in the method;
5. retain unfiltered raw data.

## 5. Delay, gain and correlation

### Delay

For a paired local/remote record, calculate normalized cross-correlation over
the bounded search window. The lag of the selected peak gives sample delay:

```text
delay seconds = lag samples / sample rate
```

State sign convention, such as positive when remote trails local. When boards
are unsynchronized, include alignment/reference uncertainty. Report local
trigger delay and remote relative delay separately when needed.

### Gain

Choose one plan-defined amplitude estimator:

- signed peak;
- peak-to-peak;
- RMS over the event window;
- fitted template scale.

For remote/local path transfer:

```text
gain ratio = remote amplitude / local amplitude
```

Do not compare readings with different calibration/termination without
correcting and documenting them. Avoid gain division when the local denominator
is below the baseline qualification threshold.

### Correlation

Use normalized correlation:

```text
rho = sum((x-mean(x)) (y-mean(y))) /
      sqrt(sum((x-mean(x))^2) sum((y-mean(y))^2))
```

Report the search window, lag, whether sign inversion was permitted and how
multiple peaks were resolved. Keep polarity as a separate result; do not take
absolute correlation and then call polarity positive.

## 6. Polarity, width, energy and ringing

### Polarity

Determine sign from the plan-defined leading excursion or fitted template.
Report `positive`, `negative` or `bipolar` consistently. Account for known
inverting couplers/amplifiers in the declared path.

### Width

Use one method across runs:

- full width at half maximum;
- threshold crossing duration;
- fitted impulse-envelope width.

State threshold, interpolation and whether ringing lobes are excluded. Report
seconds, not just sample count.

### Energy

After baseline removal:

```text
energy V2s = sum(v[n]^2) / sample_rate
```

Use the same bounded event window for comparisons. Call this signal-energy
index unless impedance is known; it is not joules by itself.

### Ringing

Estimate ringing only after the main impulse:

1. select the documented tail window;
2. remove remaining DC/trend;
3. apply the fixed window function if specified;
4. find the dominant frequency or fit a damped sinusoid;
5. require enough cycles and signal-to-noise for the estimate.

Report zero/unavailable according to the method when no qualified ringing is
present. Do not infer ringing frequency from the generator setting alone; that
value is reconstructed.

## 7. Phase

Define the reference:

- generator base sine zero crossing;
- external line-cycle reference;
- shared trigger;
- local measured reference waveform.

Calculate event phase with a hardware/event timestamp where possible:

```text
phase degrees = 360 × fractional_part(event_time / reference_period)
```

Use circular statistics for multiple events. Do not average 359 degrees and
1 degree arithmetically. Report the reference source and timing uncertainty.

If the two boards do not share the required time/phase reference, report remote
absolute phase as unavailable rather than using host UTC.

## 8. Dead time and data loss

### Detector dead time

Use one reviewed method:

- configured hardware dead-time readback plus verification;
- deterministic two-pulse interval sweep;
- event descriptor timestamps showing the minimum resolvable separation.

Do not derive detector dead time from the application polling interval.

### Data loss

Track independently:

- generator emitted counter;
- local/remote detected counters;
- hardware drop counters;
- FIFO high-water and overflow sticky flags;
- sequence gaps;
- unpaired waveform records;
- service/process restarts and epoch changes.

Use the acceptance-plan definition. A common observed loss fraction is:

```text
loss fraction = max(0, emitted - detected) / emitted
```

but it does not replace hardware drop/sequence evidence. Report both. A zero
software gap count is not proof of zero FPGA drops.

For long-running qualification, periodically snapshot coherent 64-bit counters
and FIFO state. Never reset lifetime totals to make a graph start at zero; start
a new monitoring epoch.

## 9. Reset and rollover

Run counter checks only after safe stop and output-disabled verification.

### Reset

Use the contract's documented command and expected behavior:

- lifetime count preserved with epoch advanced;
- per-epoch count cleared;
- FIFO cleared without lifetime reset;
- another explicitly documented result.

Record before/after counter and epoch values. Do not assume a dashboard reset
changes hardware history.

### Rollover

Use the approved boundary-injection/simulation method when available. Do not
wait for or force an uncontrolled real-world wrap.

Verify modular arithmetic, high/low snapshot coherence and epoch behavior at
the declared counter width. Record method and before/after values.

## 10. Report interpretation

`build_qualification_report.py` emits:

- normalized evidence JSON with immutable plan/observation bindings and an
  evidence-core SHA-256;
- CSV containing every measurement, provenance and criterion result;
- Markdown operator report with board, driver, safety, integrity and failed
  gates.

For an active run, reporting also proves that the run began inside the
request-bound authorization window, repeated the exact single-use nonce and
preserved an exclusively created plan-SHA/nonce run record. It opens the
measured acquisition-restore artifact and compares both complete endpoint
states field-for-field with the bound pre-state. A copied pre-state hash or a
Boolean `acquisition_restored` claim is not sufficient.

`PASS` means every structurally present phase, measurement, counter and safety
gate passed its declared criterion, including exact planned-versus-observed
emitted total. It does not certify an untested voltage,
frequency, cable, board, OS, driver version or long-term operating condition.

`FAIL` is a valid evidence result. Preserve it.

A tool error means the evidence package itself is inconsistent, incomplete,
tampered, unsafe or unverifiable. Fix collection/process errors by creating new
artifacts. Do not edit measured values to satisfy the validator.
