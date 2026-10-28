# Application access-control policy

## Default: no application token

Set:

```json
"application": {
  "authentication": "none"
}
```

This is the default for generated projects. In this mode:

- do not create, generate, request, paste, cache, transmit or validate an
  operator token;
- do not add a token input, token-file path, bearer header, token cookie or
  token-dependent button state;
- do not silently promote a deployment receipt, qualification nonce, SSH
  password or physical-output authorization into a dashboard credential;
- keep read-only status and safe local controls usable according to their
  actual hardware/API gates.

`none` does not mean “ignore security.” Keep services loopback-bound behind the
board's same-origin Nginx route, validate every mutation, apply Origin and CSRF
defenses, restrict network exposure, use least privilege, and preserve all
hardware safety gates.

## Explicit opt-in: operator token

Select:

```json
"application": {
  "authentication": "operator-token"
}
```

only when the user or system security design explicitly requires
application-level credentials. Do not infer this choice from the presence of
Start, Stop, Reset or physical-output controls.

Before implementation, define:

- which routes/actions are protected and which status endpoints remain public;
- how the token is provisioned without embedding it in source, packages, URLs,
  logs or browser history;
- transport protection; bearer tokens sent over plain HTTP can be observed on
  the network;
- token lifetime, rotation, revocation and service-restart behavior;
- storage and file permissions;
- browser memory/persistence behavior;
- failure status and operator recovery;
- audit fields that identify a principal without recording the secret.

Missing or invalid credentials must return a specific HTTP status and stable
error code. The UI must say “operator token required” or “token rejected”; it
must not report “FPGA unknown,” “no samples,” or “service offline” for an access
failure.

## Three independent authorization layers

Never merge these:

1. **Application access control** — `none` or `operator-token`; controls who may
   call selected app routes.
2. **Deployment authorization** — staged-plan hashes, receipts and commit
   approval; controls whether files/services may be changed on a live board.
3. **Physical-output authorization** — exact board identity, wiring,
   calibration, amplitude/time bounds, nonce/lease and safe-stop evidence;
   controls whether a physical output test may energize hardware.

Changing application authentication never weakens the other two layers.

## UI state model

Evaluate action availability as separate gates:

```text
action_available =
  not busy
  and api_compatible
  and access_allowed
  and hardware_ready_if_required
  and physical_authorization_valid_if_required
```

Expose every false term as a visible reason near the action. Do not hide
controls merely because one gate is false, and do not show a disabled button
without an explanation.

Keep waveform provenance separate:

- `configured_preview`: reconstructed from settings, never measured;
- `simulation`: software-generated values, not a physical ADC capture;
- `measured_adc`: samples returned by the selected physical input;
- `none`: no capture has been requested or returned.

An empty IN1 graph is expected when OUT1 is not physically driven. A token or
UI lock cannot create a measured trace, and a successful simulation is not
evidence of DAC or ADC operation.

## Validation

The app validator rejects recognizable token prompts and bearer-token gates
when `application.authentication` is `none`. It warns when
`operator-token` is selected but no recognizable runtime boundary exists.

Tests must cover:

- default scaffolds contain no token UI, token request or bearer header;
- `none` plus hidden token gating fails validation;
- token mode never blocks public status unless the declared policy says so;
- missing, invalid, expired and rotated tokens produce distinct evidence;
- application access failure, hardware-not-ready and acquisition-empty states
  render different messages;
- deployment and physical-output gates remain enforced in both modes.
