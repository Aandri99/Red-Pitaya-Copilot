# RTL and register-contract architecture

Use one versioned contract to connect RTL, Linux services, Python tools and application documentation.

## Contents

1. Layered architecture
2. Register-block rules
3. Required identity and safety registers
4. Access semantics
5. Atomic values and counters
6. Contract file
7. Generated bindings
8. Safe control sequence
9. Review checklist

## 1. Layered architecture

Split a serious design into:

```text
physical interfaces
  -> sample normalization
  -> application DSP/event logic
  -> queues and counters
  -> AXI/interrupt adapter
  -> Processing System
```

Keep these concerns separate:

- ADC unpacking and sign extension;
- clocking/reset;
- filtering/detection;
- event descriptor construction;
- FIFO/DMA transport;
- register decoding;
- DAC mux and safety;
- board-model top-level wiring.

The application DSP module should be simulatable without the Zynq Processing System.

## 2. Register-block rules

Use byte offsets from one base address. A 32-bit AXI-Lite register normally advances by four bytes:

```text
0x0000, 0x0004, 0x0008, ...
```

Never mix word indexes with byte offsets in naming or documentation.

For AXI-Lite:

- accept address and data channels according to protocol, not assuming same-cycle arrival;
- honor `WSTRB` for writable bytes;
- return a defined value for every implemented read;
- return an error or zero for unimplemented addresses according to the parent bus design;
- avoid combinational paths from asynchronous hardware directly to AXI read data;
- synchronize or snapshot status first;
- generate a single-cycle pulse for command registers;
- keep config registers persistent until reset or software write;
- separate sticky errors from live status.

Use local address decoding relative to the assigned base. Do not repeat absolute addresses throughout RTL.

## 3. Required identity and safety registers

New custom register blocks should expose at least:

| Name | Access | Purpose |
|---|---|---|
| `MAGIC` | RO | Constant identifying the intended design |
| `ABI_VERSION` | RO | Major/minor register-contract version |
| `CAPABILITIES` | RO | Feature bits for optional logic |
| `CONTROL` | RW | Enable, override and operating controls |
| `STATUS` | RO | Ready, active and live error state |

Recommended diagnostics:

| Name | Access | Purpose |
|---|---|---|
| `COMMAND` | PULSE/WO | Reset epoch, snapshot, clear FIFO |
| `ERROR_STICKY` | W1C | Latched hardware faults |
| `EVENT_COUNT_LO/HI` | RO snapshot | Authoritative detected events |
| `DROP_COUNT_LO/HI` | RO snapshot | Descriptors not retained |
| `FIFO_LEVEL` | RO | Current entries |
| `FIFO_HIGH_WATER` | RO | Maximum level since epoch |
| `SEQUENCE` | RO | Last emitted descriptor sequence |
| `EPOCH` | RO | Monitoring-generation identifier |
| `TIMESTAMP_LO/HI` | RO snapshot | Coherent sample-clock time |

Define `CONTROL` so reset/default is safe:

- custom output override = 0;
- generator enable = 0;
- detector configuration may be loaded but inactive;
- interrupts masked unless the system explicitly expects them.

Never make “enable output” the reset value.

### ABI version

Represent a 32-bit ABI version as:

```text
bits 31:16 = incompatible major version
bits 15:0  = backward-compatible minor version
```

Software must reject a different major version. Software may use a capabilities bit and minimum minor version for optional features.

## 4. Access semantics

The bundled generator accepts:

| Access | Meaning |
|---|---|
| `ro` | hardware-produced, software read-only |
| `rw` | persistent software configuration |
| `wo` | write-only payload |
| `w1c` | writing 1 clears selected sticky bits |
| `w1s` | writing 1 sets selected bits |
| `rc` | read clears; avoid for diagnostics unless necessary |
| `pulse` | accepted write creates one clock-domain command pulse |

Document side effects. Do not describe `pulse`, `w1c` or `rc` as ordinary RW.

For each field define:

- bit position and width;
- reset value;
- units and scale;
- legal range;
- behavior while disabled;
- behavior on illegal write;
- clock domain;
- atomicity relation to other fields.

Prefer integer fixed-point units:

```text
threshold ADC counts
dead time sample clocks
frequency tuning word
duration sample clocks
timestamp sample clocks
```

Convert to volts/seconds in software with explicit calibration and sample frequency.

## 5. Atomic values and counters

### Multiword counters

Do not read a free-running 64-bit counter as unrelated high/low words. Use one of:

1. software writes `SNAPSHOT`; hardware copies all counters into a stable bank;
2. reading low latches high until both are read;
3. software uses high-low-high retry and accepts only equal high reads.

Use a single snapshot command for related values so event count, drop count, sequence and timestamp describe one instant.

### Counter behavior

For long monitoring:

- use at least 64 bits for event totals and timestamps;
- saturate fault counters or explicitly expose wrap/epoch;
- never reset lifetime totals merely because a descriptor FIFO is cleared;
- increment event total at detector acceptance;
- increment drop total when an accepted event cannot be represented/queued;
- maintain a descriptor sequence that advances for every accepted event, including dropped descriptors if the gap must expose loss;
- expose an epoch that changes on explicit monitoring reset or FPGA reload.

### Cross-domain counter export

Do not independently synchronize each bit of a binary counter. Use:

- Gray encoding plus synchronized Gray capture;
- request/ack snapshot handshake;
- asynchronous FIFO descriptor;
- stable sampling with a verified protocol.

## 6. Contract file

Start with `assets/register-contract.example.json`.

Top-level fields:

```json
{
  "schema_version": 1,
  "namespace": "pd_acq",
  "base_address": "0x40600000",
  "address_span": "0x00010000",
  "bus_width_bits": 32,
  "registers": []
}
```

Each register:

```json
{
  "name": "CONTROL",
  "offset": "0x000C",
  "access": "rw",
  "reset": "0x00000000",
  "description": "Safe control bits",
  "fields": [
    {
      "name": "ENABLE",
      "lsb": 0,
      "width": 1,
      "description": "Enable detector"
    }
  ]
}
```

The validator rejects:

- malformed names;
- invalid integer strings;
- misaligned or duplicate offsets;
- registers outside the span;
- unsupported access modes;
- reset values wider than the bus;
- empty descriptions;
- overlapping/out-of-range fields;
- duplicate field names;
- missing required identity/control registers in strict mode.

Run:

```text
python scripts/register_contract.py validate path/to/register-contract.json --strict
```

Do not edit generated bindings to work around a failed contract. Fix the contract and regenerate.

## 7. Generated bindings

Generate:

```text
python scripts/register_contract.py generate contract.json --out-dir generated
```

Outputs include:

- SystemVerilog include with relative offsets and field masks;
- C header with base/offset/mask constants;
- Python module with the same constants;
- canonical JSON;
- Markdown register reference.

Check generated files into the project unless its build deterministically generates them before every compile. Place a “generated; do not edit” marker in each output.

In RTL:

```systemverilog
`include "pd_acq_regs.svh"

case (axi_addr_relative)
  PD_ACQ_REG_CONTROL: ...
  PD_ACQ_REG_STATUS:  ...
endcase
```

In software, map only the declared `address_span`. Use page-aligned `mmap` calculations and bounds-check every access.

## 8. Safe control sequence

Use this sequence for generators or outputs:

```text
write CONTROL.ENABLE=0 and OUTPUT_OVERRIDE=0
write configuration registers
issue COMMAND.SNAPSHOT or APPLY if provided
read back configuration/status
clear understood sticky flags
enable detector/internal logic
verify ready and no errors
enable output override only in an authorized measured test
```

Stopping:

```text
disable output override first
disable generator/custom output path
wait for safe status if defined
mask interrupts
optionally clear FIFO
do not reset lifetime counters unless explicitly requested
```

Configuration writes must not create a partially updated live waveform. Use inactive-bank/apply semantics when multiple words form one configuration.

## 9. Review checklist

- Base/span do not overlap another slave.
- Contract base equals Vivado address assignment and software mapping.
- All offsets are byte offsets and bus-aligned.
- `MAGIC` and ABI are constants in hardware.
- Required features have capability bits.
- Reset disables physical outputs.
- Every command has exact pulse behavior.
- W1C flags clear only written bits.
- Partial writes honor strobes.
- Multiword values are coherent.
- Counters have explicit reset/wrap/saturation behavior.
- CDC is explicit for every non-AXI signal.
- Unknown reads/writes have defined behavior.
- Generated C/Python/SV constants match the contract hash.
- Unit and scale are documented.
- Software rejects incompatible ABI/magic.

## Red Pitaya `sys_bus_if` slave contract

Use the interface clock and reset as the single authoritative contract:

```systemverilog
module my_slave(sys_bus_if.s bus);
  always_ff @(posedge bus.clk) begin
    if (!bus.rstn) begin
      bus.ack   <= 1'b0;
      bus.rdata <= '0;
    end else begin
      bus.ack <= bus.wen | bus.ren;
      // register logic
    end
  end
endmodule
```

Do not add parallel `clk_i`/`rstn_i` ports wired to the same top-level nets and
then create a second local reset synchronizer. A duplicated reset contract can
leave the registered ACK path unavailable in hardware while a simplified RTL
test still appears healthy.

Required evidence:

1. instantiate `sys_bus_if` once with the intended clock/reset;
2. consume `bus.clk`/`bus.rstn` inside the slave;
3. simulate reset assertion, release, and the first legal identity read;
4. prove ACK/data before the interconnect timeout;
5. inspect the routed checkpoint to confirm ACK/reset connectivity and the
   expected bus clock on the slave's sequential cells.
