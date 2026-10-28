# API and hardware-control safety

## Contents

1. Threat and failure model
2. Request rules
3. Safe command ordering
4. Stop contract
5. Shell and filesystem
6. Secrets and remote access
7. Audit fields

## Threat and failure model

Assume:

- the LAN may be untrusted;
- browser requests can be forged/replayed;
- fields can have wrong type, NaN/infinity, huge values, or unexpected units;
- two clients can race;
- service restart can occur between command and response;
- a timeout does not prove the command failed;
- hardware can remain in its last state after software crashes.

## Request rules

For every mutation define:

- HTTP method/path;
- application access-control requirement (`none` or explicit
  `operator-token`) and the separate physical/deployment authorization gates;
- exact JSON schema;
- accepted units and numeric bounds;
- command ID/idempotency semantics;
- current states from which it is legal;
- side effects and ordering;
- timeout behavior;
- safe rollback;
- response/error codes.

Reject:

- unknown fields when they can hide typos;
- booleans represented as arbitrary strings;
- non-finite numbers;
- numeric strings unless explicitly part of the contract;
- paths, commands, register addresses, hostnames, or ports from untrusted input;
- requests over the size limit.

For browser-facing mutations, require all of the following unless an equally
strong, documented authentication boundary replaces them:

- `Content-Type: application/json`;
- a custom non-simple header such as `X-RedPitaya-Request: 1`;
- an exact same-origin `Origin` check when the browser supplies `Origin`;
- a bounded, explicit `Content-Length`;
- connection close on any rejection that occurs before the declared body has
  been consumed.

The custom header and same-origin checks are CSRF defenses, not user
authentication. Application-level authentication defaults to `none`; do not
invent a temporary token merely because a route mutates state. If the user
explicitly selects `operator-token`, follow
[authentication-policy.md](authentication-policy.md) and prove the token
boundary before deployment. Independently verify whether the board's existing
web-access boundary covers the custom route. Network exposure, least privilege,
request validation and physical-output authorization remain mandatory in both
application-authentication modes.

For a receipt-bound physical-output test, consume a fresh single-use nonce by
exclusive creation before the first output write. Bind the record to the exact
deployment/qualification plan hash, board identities, physical calibration,
writer/executor and expiry. Reject an existing record, and require the
authorization to remain valid through the entire requested output lease.
Status/readiness endpoints must apply the same non-mutating gates before
displaying `ready`: current board identity, expiry with enough time for the
minimum lease, and absence of the durable consumption record. A structurally
valid but expired, wrong-board, or already-consumed authorization is not ready.

## Safe command ordering

For hardware output changes:

1. authenticate/authorize;
2. validate full request;
3. acquire ownership;
4. verify model/API/FPGA version;
5. disable output/override;
6. apply bounded configuration;
7. read back critical values;
8. enable internal logic;
9. enable physical override/output last;
10. return confirmed state.

On any error, attempt safe disable and report whether it was confirmed.

Name the confirmed state precisely. Disabling one FPGA override proves only
that override is off; it does not prove an electrically quiet connector if an
ASG, PID, DMA path, relay, or another writer can still own the output. Report
separate fields such as `pd_override_disabled` and
`electrical_output_safe`. Leave the latter `null`/unknown until every possible
producer and the physical output have been independently checked.

## Stop contract

Stop must:

- be callable repeatedly;
- work during partial startup;
- cancel future scheduled work;
- disable the physical/output override;
- leave counters/history unchanged unless a separate reset was requested;
- return confirmed safe state or a fault that clearly says confirmation failed.

Make Stop dominant over an in-flight Start. Latch cancellation before waiting
for the hardware-writer lock; Start must check that latch under the same lock
before configuration, immediately before physical enable and after enable
readback. Add deterministic race tests. A Stop response must never be followed
by a previously accepted operation enabling output.

Keep separate endpoints for:

- stop/disable;
- reset session counters;
- clear service journal;
- reset cloud/dashboard history;
- load FPGA.

Combining these actions makes recovery destructive and ambiguous.

## Shell and filesystem

Avoid shell execution. If a fixed helper is necessary:

- use an absolute executable path;
- pass an argument vector, not a shell string;
- allow-list every token;
- set timeout/output limits;
- clear or explicitly define environment;
- verify exit status;
- never include credentials in output.

Resolve and validate paths under an exact allowed directory. Reject traversal, symlinks where ownership is uncertain, and broad recursive operations.

Treat replies from a remote instrument as untrusted input. Never concatenate a
queried SCPI setting directly into a restore command: parse it into a bounded
numeric or allow-listed enum, serialize a canonical value, forbid command
separators/control characters, restore, and query again to prove the complete
pre-state was recovered. If that proof fails, fail the qualification rather
than reporting restoration complete.

If an application restores only a reviewed subset of settings, label it
exactly as subset restoration in the API and UI. Do not shorten that claim to
"SCPI restored" or "pre-state restored"; complete restoration remains a
qualification gate.

## Secrets and remote access

- Do not hardcode passwords/tokens.
- Do not log Authorization headers.
- Do not expose state-changing APIs with wildcard CORS.
- Bind services to loopback unless remote access is designed.
- Use the existing Red Pitaya authentication boundary only after verifying it covers the custom route.
- Do not request an operator token unless
  `application.authentication` is explicitly `operator-token`.
- Never use an application token as a substitute for deployment approval or a
  receipt-bound physical-output authorization.

## Audit fields

Record for important mutations:

- timestamp;
- service epoch;
- command ID;
- authenticated principal if available;
- old and confirmed new state;
- sanitized parameters;
- outcome/error code.

Do not record secrets or raw high-volume waveforms in the control audit.
