# Device-tree overlays for custom FPGA logic

Use a matching overlay when custom logic adds interrupts, DMA, reserved memory or a kernel-visible peripheral.

## Contents

1. Decide whether an overlay changes
2. Model the hardware contract
3. Interrupts
4. DMA and reserved memory
5. Build and validate
6. Loading behavior
7. Failure recovery
8. Checklist

## 1. Decide whether an overlay changes

An unchanged device tree may be sufficient when:

- custom RTL replaces logic behind an existing userspace-mapped address window;
- no kernel driver binds to it;
- no interrupt/DMA/reserved memory changes;
- the existing overlay exposes exactly the required bus ranges.

Create/update an overlay when:

- a new address range must be described to Linux;
- a UIO or kernel driver binds to custom IP;
- an interrupt reaches the Processing System;
- DMA needs channels/coherency metadata;
- FPGA writes a reserved DDR region;
- clocks/resets referenced by a driver change;
- the FPGA region/firmware property expected by the loader changes.

Never reuse a stock `.dtbo` by filename alone. Compare source semantics.

## 2. Model the hardware contract

Overlay source normally contains fragments targeting nodes in the base tree. Exact labels vary by OS/device tree. Inspect the matching release’s `.dtso` or decompile the exact `.dtbo`.

A conceptual custom peripheral node:

```dts
custom_acq@40600000 {
    compatible = "generic-uio";
    reg = <0x40600000 0x00010000>;
    interrupt-parent = <&intc>;
    interrupts = <0 61 4>;
    status = "okay";
};
```

Do not copy these example addresses/interrupt numbers. Use Vivado address assignment and the Zynq interrupt connection for the exact design.

Verify:

- base and span equal the register contract;
- no overlap with another PL peripheral;
- address/size cell widths match the parent bus;
- compatible string matches the intended driver;
- node status is enabled;
- interrupt specifier and trigger type match hardware;
- DMA/coherency properties match the driver.

### UIO versus custom driver

Use UIO for simple register/interrupt access when:

- bounded MMIO and one/few interrupts are sufficient;
- no complex DMA/cache lifecycle is required;
- userspace ownership is exclusive.

Use a kernel driver when:

- DMA API/cache handling is required;
- multiple processes need arbitration;
- security/isolation matters;
- power/clock/reset frameworks are involved;
- robust interrupt and buffer lifecycle are needed.

Do not expose arbitrary physical memory to untrusted applications.

## 3. Interrupts

Trace:

```text
custom IP interrupt
 -> concat/vector bit
 -> Zynq IRQ_F2P
 -> GIC SPI number
 -> overlay interrupt specifier
 -> driver/UIO
```

Confirm vector ordering in the exact block design. A one-bit shift can bind a different device interrupt.

Define whether the source is:

- level-high;
- level-low;
- rising edge;
- falling edge.

The device-tree trigger must match. A pulse source may be missed unless latched/stretched by hardware or captured by the interrupt controller. For queues, use a level or sticky pending bit with W1C acknowledgement.

Test:

- masked source does not interrupt;
- event asserts expected status;
- driver receives interrupt;
- acknowledgement clears it;
- a new event during acknowledgement is not lost;
- storm prevention works.

## 4. DMA and reserved memory

For FPGA writes to a fixed DDR ring, reserve memory so Linux does not allocate it. Conceptual base-tree/overlay structure:

```dts
reserved-memory {
    #address-cells = <1>;
    #size-cells = <1>;
    ranges;

    acquisition_buffer: buffer@1e000000 {
        reg = <0x1e000000 0x01000000>;
        no-map;
    };
};
```

Again, do not copy example addresses. Calculate against the exact board memory map and boot configuration.

Check:

- region is inside physical DDR;
- region does not overlap kernel, CMA, initrd, framebuffer or other reservations;
- size/alignment meet DMA requirements;
- `no-map` behavior matches the driver;
- hardware address width covers the region;
- driver obtains/maps it consistently;
- cache attributes and barriers are correct.

Prefer standard DMA bindings and a kernel driver when possible.

## 5. Build and validate

Compile overlay source with symbols support as required by the base tree:

```text
dtc -@ -I dts -O dtb -o fpga.dtbo fpga.dtso
```

Then inspect:

```text
dtc -I dtb -O dts -o decoded.dtso fpga.dtbo
```

Validation:

- file begins with flattened-device-tree magic `0xd00dfeed`;
- source compiles without unresolved labels;
- decompiled `reg` and `interrupts` match intended values;
- fragment targets exist in the exact OS base tree;
- overlay can be applied/removed according to loader policy;
- device/driver appears only after successful FPGA configuration;
- no unexpected kernel warnings.

Treat a warning about address/size cells as a real defect until understood.

The bundled packager checks DTBO magic but cannot prove semantic compatibility.

## 6. Loading behavior

For both target releases, inspect `/opt/redpitaya/sbin/overlay.sh` from the exact image/board. It coordinates FPGA Manager/fpgautil and overlay application.

Known supplied-image filenames:

| OS | Bitstream | Overlay |
|---|---|---|
| 2.x (verified 2.00-35 and 2.07-48) | `fpga.bit.bin` | `fpga.dtbo` |
| 3.00-57 | `fpga.bin` | `fpga.dtbo` |

Do not use the OS 2 artifact name merely because general documentation shows it. OS 3.00-57’s observed loader profile expects `fpga.bin`.

Use `overlay.sh` rather than low-level FPGA Manager writes unless a tested deployment workflow requires `fpgautil`. Keep the bitstream and overlay from the same build/contract.

After authorized load, verify:

```text
/sys/class/fpga_manager/fpga0/state
/tmp/update_fpga.txt
/tmp/loaded_fpga.inf
/proc/device-tree/... custom node
/dev/uio* or driver binding
dmesg relevant lines
```

Paths/status files can vary. Use the target loader implementation.

## 7. Failure recovery

Prepare known-good bitstream/overlay and exact rollback command before testing.

If load fails:

1. keep custom physical outputs disabled;
2. capture FPGA Manager state and loader output;
3. inspect kernel log;
4. remove a partially applied custom overlay only through the loader’s supported path;
5. reload the known-good stock project;
6. verify manager state and stock magic/function;
7. do not repeatedly force-load incompatible artifacts.

If Linux hangs or network access disappears, use the board’s established recovery method and restore the SD card if necessary. Do not experiment with boot-time automatic loading until transient loading is reliable.

## 8. Checklist

- Exact base device tree identified.
- Overlay source stored, not only binary.
- Bitstream and overlay share one build record.
- `reg` equals Vivado assignment/contract.
- Interrupt path and trigger type traced end to end.
- Reserved memory checked against exact OS memory layout.
- Overlay compiles and decompiles cleanly.
- DTBO magic valid.
- OS-specific artifact name correct.
- Loader command derived from exact OS.
- Known-good rollback ready.
- Outputs safe throughout load.
