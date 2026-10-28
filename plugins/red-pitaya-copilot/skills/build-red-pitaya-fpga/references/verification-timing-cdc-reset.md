# Verification, timing, CDC and reset

Use these gates before packaging any hardware artifact.

## Contents

1. Verification ladder
2. Self-checking simulation
3. Assertions and coverage
4. Reset architecture
5. Clock-domain crossing
6. Timing constraints and reports
7. Synthesis/implementation review
8. Release evidence

## 1. Verification ladder

Do not jump directly from compiling RTL to hardware:

1. static syntax/lint;
2. module-level self-checking simulation;
3. CDC/reset structural review;
4. integration simulation;
5. synthesis;
6. post-synthesis timing sanity;
7. implementation;
8. full timing/DRC/utilization review;
9. packaged-artifact validation;
10. staged hardware acceptance.

Each level catches different failures. “Vivado generated a bitstream” proves only that the tool completed under its configured checks.

## 2. Self-checking simulation

Every testbench must:

- generate all relevant clocks and resets;
- drive legal and illegal sequences;
- compare output to a reference or explicit expected values;
- use bounded timeouts;
- report failures clearly;
- return nonzero on failure;
- end without manual waveform inspection.

### Required scenario categories

Reset:

- initial reset asserted;
- synchronous release;
- reset during idle;
- reset during active processing;
- reset while FIFO contains data;
- reset near a CDC handshake;
- repeated reset.

Data:

- zero, full-scale positive and full-scale negative;
- signed boundary values;
- threshold minus one/equal/plus one;
- minimum and maximum durations;
- timestamp rollover;
- counter rollover or saturation.

Control:

- partial byte writes;
- back-to-back writes;
- read during live updates;
- command pulse once;
- illegal/reserved fields;
- enable-disable-enable.

Queue:

- empty read;
- first entry;
- full;
- simultaneous enqueue/dequeue;
- overflow;
- sustained producer faster than consumer;
- consumer stall;
- recovery and loss accounting.

CDC:

- unrelated initial clock phase;
- faster source/slower destination;
- slower source/faster destination;
- close frequencies with phase walk;
- pulses near destination edges.

### Test organization

Keep:

```text
rtl/        synthesizable sources
tbn/        testbench and behavioral models
sim/        scripts and expected results
generated/  register bindings
```

Use fixed random seeds in CI and optionally additional recorded seeds in stress runs.

## 3. Assertions and coverage

Add assertions for:

- output override is off while reset or safe-stop is active;
- FIFO level never exceeds depth;
- accepted event increments the authoritative event counter once;
- failed descriptor enqueue increments drop counter once;
- no FIFO read when empty unless explicitly ignored;
- no FIFO write when full unless drop policy applies;
- command pulse is one destination-clock cycle;
- request is not issued twice before acknowledgment;
- Gray pointer changes at most one bit per increment;
- mutually exclusive state/output modes;
- reserved register bits read as documented.

Add cover points for:

- first event after reset;
- simultaneous push/pop;
- full then drain;
- overflow observed;
- timestamp wrap;
- counter high-word change;
- software snapshot during activity;
- enable after configuration.

Assertions belong close to the module or in a bound checker. Do not disable a failing assertion to make a regression green without resolving or formally waiving it.

## 4. Reset architecture

### Principles

- Asynchronous assertion may be necessary for hardware safety.
- Deassert reset synchronously in each clock domain.
- Never distribute one asynchronously released reset to unrelated domains without per-domain synchronization.
- Keep output-enable flops in a reset state that physically disables output.
- Reset configuration and state deliberately; do not rely on FPGA initialization values alone when runtime reload/reset matters.

### Per-domain reset synchronizer

Use an approved synchronizer structure with `ASYNC_REG` attributes as appropriate:

```text
external/global reset asserts both synchronizer stages
each active clock shifts inactive level through stages
local reset releases only after the final stage
```

Do not gate clocks to create reset behavior. Use clock enables or supported clock-buffer control.

### Reset dependencies

Release in a defined order:

1. stable input/reference clock;
2. PLL/MMCM lock;
3. interconnect reset;
4. peripheral/DSP reset;
5. software-visible ready;
6. output enable only by explicit software action.

If a clock can stop, define how its domain reaches safe state. A synchronous reset cannot act without its clock.

## 5. Clock-domain crossing

Classify every crossing:

| Transfer | Safe structure |
|---|---|
| Static/seldom-changing one-bit level | two-or-more-flop synchronizer |
| One-cycle pulse | toggle synchronizer, request/ack or pulse stretcher with proven bounds |
| Related multi-bit config | request/ack stable-bus snapshot |
| Counter | Gray counter or snapshot handshake |
| Continuous data stream | asynchronous FIFO |
| Reset | asynchronous assert, synchronous release per domain |

### Unsafe structures

Do not use:

- a two-flop synchronizer on every bit of a changing binary bus;
- direct pulse synchronization when the pulse can be shorter than the destination period;
- combinational logic between synchronizer stages;
- unsynchronized FIFO pointers;
- a false-path constraint as a substitute for a CDC structure;
- software delay as a hardware CDC protocol.

### CDC documentation

For each crossing record:

```text
source clock:
destination clock:
payload:
maximum source rate:
chosen structure:
latency:
overflow/loss behavior:
constraint:
test:
```

Review Vivado CDC output and locate every waived path in source. A warning count alone is not a review.

## 6. Timing constraints and reports

Define every primary and generated clock with its real frequency and source relationship.

Constrain:

- input/output delays where external timing matters;
- generated clocks after MMCM/PLL/dividers;
- asynchronous clock groups only when truly unrelated;
- multicycle paths only when RTL behavior guarantees the exception;
- false paths only for verified asynchronous/static structures.

Do not apply broad:

```text
set_false_path -from [get_clocks *] -to [get_clocks *]
```

or wildcard exceptions merely to obtain positive slack.

After implementation inspect:

- `report_timing_summary`;
- unconstrained paths;
- clock interaction;
- pulse width;
- methodology/DRC;
- CDC;
- utilization and congestion;
- high fanout and replication;
- power/thermal estimate when the design is materially larger.

Read the top-level **Design Timing Summary** first. Do not copy a passing
sub-clock, path group, or later detailed section and present it as whole-design
closure. Require setup and hold timing to pass for release. Record:

```text
WNS:
TNS:
failing setup endpoints:
WHS:
THS:
failing hold endpoints:
unconstrained internal endpoints:
no-clock checks:
inputs without input delay:
outputs without output delay:
```

Zero WNS/TNS violations are not sufficient when constraint coverage is
incomplete. Treat invalid clock-object queries, empty false-path targets,
unconstrained internal endpoints, unexplained no-clock checks, and missing
external interface delays as release blockers. Intentional asynchronous/static
interfaces require narrow source-linked waivers; a broad wildcard waiver is
not acceptable.

If timing is not met or constraint coverage is incomplete, the artifact is
experimental and must not be packaged as a release.

## 7. Synthesis/implementation review

Investigate:

- inferred latches;
- trimmed logic that should be observable;
- undriven or multiply driven nets;
- width truncation/sign warnings;
- inferred RAM implementation;
- DSP inference;
- clock used as data or data used as clock;
- unconstrained generated clock;
- combinational loops;
- excessive fanout;
- reset on large RAMs preventing BRAM inference;
- critical warnings from block-design validation.
- generated IP or DCPs customized for a different FPGA part than the selected
  board target;
- I/O placement critical warnings, especially when a physical output is in
  scope;
- DSP pipeline/feedback warnings on the actual worst timing path.

Resource headroom matters. A design near device capacity can route unpredictably and block debug instrumentation. Report percentages and the limiting resource.

For counters and FIFOs:

- verify intended BRAM/LUTRAM inference;
- verify FIFO flags match configured read mode;
- check first-word fall-through assumptions;
- test latency used by the AXI reader;
- review RAM collision semantics.

## 8. Release evidence

Store or summarize:

- test list and pass/fail;
- simulator and seed;
- lint findings;
- CDC report with waivers;
- timing summary;
- DRC/methodology findings;
- utilization;
- Vivado version;
- build command and commit;
- artifact hashes.

Classify every warning:

```text
fixed
accepted with reason
tool false positive with evidence
not applicable to released configuration
unresolved blocker
```

Do not claim a warning is harmless merely because a stock project also reports it.
