# Limits and truthful claims

## Application versus instrument

A web application is not by itself:

- a real-time controller;
- a continuous acquisition engine;
- a calibrated measurement chain;
- electrical isolation or protection;
- a safety interlock;
- proof of OS/model compatibility.

State which layer provides each capability.

## Timing

Browser, HTTP, Lua, Python, C++, and general-purpose Linux scheduling have variable latency. They can supervise or configure deterministic FPGA logic but cannot guarantee per-sample response.

SCPI or repeated triggered buffers have re-arm/transfer gaps unless measured architecture proves otherwise. WebSocket reduces transport overhead but does not eliminate acquisition gaps or make delivery durable.

## Persistence

Browser state disappears on close/reload. Nginx worker-local Lua state is not durable or necessarily shared. A systemd service can persist work only if its state, crash recovery, and storage behavior are implemented and tested.

## Compatibility

Source compatibility does not imply binary compatibility. Pin:

- OS release;
- board model/generation;
- architecture/ABI;
- Python version;
- glibc/libstdc++;
- Red Pitaya API;
- controller framework;
- Nginx modules/config;
- FPGA/register contract.

The verified OS 2.00-35 and OS 2.07-48 targets share the OS 2.x
`fpga.bit.bin`/`fpga.dtbo` artifact family. Loader command interfaces and
native application binaries still require live inspection. OS 3.00-57 uses a
separate `fpga.bin` packaging profile. Do not infer board-model or electrical
compatibility from the major OS version.

On live OS 2.00-35 Build 35 and OS 2.07-48 Build 48 boards, the stock `/bazaar?apps=` launcher
index included an application only when its directory contained a target-built
`controllerhf.so`. A valid `info/info.json`, icons, static page, Nginx fragment
and separately managed systemd service were not sufficient for a launcher
tile, although the direct `/<slug>/` route worked. Treat direct-route
availability and home-screen discoverability as separate acceptance
requirements. Do not add or copy a lifecycle shim after an immutable
deployment plan: build it for the exact target ABI, package it, and authorize a
new plan.

## Measurement

Do not label:

- ADC/DAC codes as volts without calibration/range path;
- energy proxies as joules;
- pulse amplitude as apparent charge;
- severity as failure probability;
- health index as remaining useful life;
- simulator output as a physical high-voltage discharge.

Use “prototype condition indicator,” “sensor-output simulation,” and “trend” until the full chain is calibrated and validated against labelled assets.

## Safety

Software stop is not a certified emergency stop. Red Pitaya fast outputs can retain or change state during FPGA reload, process crash, reboot, or wiring error. Define electrical limits, external attenuation/protection, safe boot behavior, and independent shutdown where consequences require them.

Do not energize outputs during routine web-route validation.

## Security

LAN access, same-origin, and CORS are not authentication. The default scaffold is a development baseline and does not claim production remote-access security. Add and verify the deployment’s authentication, authorization, TLS, update, logging, and vulnerability-management boundary.

## Generated scaffold

The scaffold:

- produces deterministic source structure and a pinned manifest;
- provides safe no-op hardware behavior;
- does not include a target-built `controllerhf.so`;
- therefore does not by itself guarantee a stock OS 2.07 launcher tile for
  static, Lua or service architectures;
- does not install systemd units or Nginx fragments;
- does not configure FPGA images;
- does not certify target compatibility;
- does not replace integration tests on the exact board/image.
