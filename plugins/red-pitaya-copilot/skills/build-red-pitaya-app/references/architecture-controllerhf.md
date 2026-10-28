# Native `controllerhf` application

## Compatibility warning

Use the native controller architecture only when Red Pitaya’s parameter/signal WebSocket framework is required.

The supplied official web-app tutorial calls the artifact `controller.so`, while inspected OS 2.07-48 and OS 3.00-57 application images use `controllerhf.so`. Treat the exact target image and a matching shipped/example app as the authority. Do not rename an artifact based only on documentation text.

## Required evidence before implementation

Collect from the exact target:

- shipped app using the native controller framework;
- controller filename and ELF metadata;
- loader/controller framework libraries;
- matching headers and sample `Makefile`;
- Nginx/app-start route and WebSocket URL;
- required exported symbols;
- API library version and link flags;
- board model and loaded FPGA expectations.

Copy source patterns, not binary artifacts. Build against the exact target filesystem or a matching sysroot.

## Lifecycle contract

Document all callbacks and make each safe:

- description query;
- initialization;
- shutdown;
- parameter get/set;
- signal get;
- parameter/signal update hooks.

During initialization:

1. Set software state to starting.
2. Put hardware output in its safe state.
3. Verify API/model/FPGA compatibility.
4. Allocate resources.
5. Start bounded workers, if any.
6. Publish ready only after all checks pass.

During shutdown:

1. Reject new starts.
2. request worker cancellation;
3. disable output;
4. join workers with a bounded timeout;
5. release API/mappings/files;
6. leave a safe final state.

Every partial initialization path must unwind previously acquired resources.

## Parameters and signals

- Keep names stable and unique.
- Define direction: browser-to-controller, controller-to-browser, or both.
- Define units, min/max, default, update rate, and reset behavior.
- Validate parameter values again in native code.
- Use signals for bounded arrays; do not send unlimited histories.
- Apply backpressure or decimation for large signals.
- Version the parameter/signal contract.

## Single-controller ownership

Official documentation warns that the web framework loads one controller module at a time. Opening another native app may unload the current controller. Therefore:

- do not use this architecture for independent always-on monitoring unless verified otherwise;
- do not assume two native apps can share hardware;
- make unload equivalent to a safe stop;
- expose continuity limitations.

## Build and ABI policy

- Treat OS 2.x as one runtime family when the target probe confirms the same
  ARM ABI, glibc generation, controller filename and dependencies. The verified
  2.00-35 and 2.07-48 targets both use the Ubuntu 22.04/glibc 2.35/Python 3.10
  family.
- Reusing one OS 2.x controller build still requires `readelf` and `ldd`
  verification on every target image; a family match is not permission to skip
  dependency checks.
- Build separately for OS 1.x, OS 2.x and OS 3.x. Do not copy OS 3 artifacts to
  OS 2; its newer glibc/toolchain can introduce unavailable symbols.
- Verify ELF32, little-endian ARM (`EM_ARM`) for a Zynq-7000 target.
- Check `readelf -h`, `readelf -d`, and `readelf -Ws`.
- Resolve every dependency against the target image.
- Do not commit an untraceable prebuilt controller.

The bundled scaffold is an interface skeleton and deliberately remains `build-for-exact-target`. Before deployment, reconcile it with the matching image’s canonical example and build it on target/sysroot.

## Acceptance

- Loader accepts the controller.
- Start/stop cycles do not leak workers or mappings.
- Browser reconnect does not reset hardware unexpectedly.
- Parameter bounds are enforced in native code.
- WebSocket disconnect returns to the documented state.
- Opening another native app produces the expected safe unload.
- Artifact origin, compiler/sysroot, OS, model, hash, and dependencies are recorded.
