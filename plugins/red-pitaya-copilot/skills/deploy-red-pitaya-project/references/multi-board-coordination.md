# Multi-board coordination

Treat every board as a separate deployment target and transaction.

## Inventory table

Create a table before any write:

| Role | Expected host | Verified fingerprint | Machine ID | Serial/MAC | OS | Model | Signal I/O | Safe stop |
|---|---|---|---|---|---|---|---|---|
| generator | ... | ... | ... | ... | ... | ... | OUT1 | ... |
| acquisition | ... | ... | ... | ... | ... | ... | IN1 | ... |
| controller | ... | ... | ... | ... | ... | ... | network | ... |

Do not use one `.local` name as evidence for another board’s role.

## Separate artifacts

Maintain per board:

- Probe record/report.
- `redpitaya-project.json` target/role interpretation.
- Deployment ID.
- Plan and file map.
- Staging root.
- Backup root.
- Current state.
- Acceptance report.

Never reuse a generated plan by changing only its hostname. Its hashes, identity, role, OS, model and mount evidence are bound together.

## Recommended order

For a generator -> acquisition -> controller/dashboard system:

1. Assert generator output disabled.
2. Verify acquisition input is within a safe range.
3. Deploy acquisition consumer first.
4. Acceptance-test acquisition status, counters and safe behavior with no external signal.
5. Deploy controller/cloud publisher.
6. Verify it can distinguish acquisition offline/quiet from healthy/zero-events.
7. Deploy generator while its DAC override/output remains disabled.
8. Verify generator status and stop.
9. Run a low-risk loopback test only with authorization.
10. Enable the intended profile in a final coordinated step.
11. Return generator output to disabled after acceptance unless continuous operation was explicitly requested.

## Parallelism

Read-only probe collection may run in parallel if results cannot be confused. Local plan generation may run in parallel.

Do not parallelize:

- Remount/commit operations on the same board.
- FPGA loads.
- Output-enable operations.
- Coordinated rollback where signal safety depends on order.

Use one operator-visible board role label in every command prompt/log.

## Failure containment

If acquisition deployment fails:

- Keep generator disabled.
- Do not test with a signal.

If generator deployment fails:

- Keep acquisition running if stable, but mark generator unavailable.
- Do not repurpose acquisition as generator.

If controller/cloud publishing fails:

- Keep authoritative acquisition counters on the acquisition board.
- Report telemetry outage separately from “no PD events.”

If identity becomes ambiguous:

- Stop all writes.
- Disable all controllable outputs.
- Re-establish physical identity and host keys independently.

## Coordinated rollback

Rollback only affected boards unless contracts became incompatible. If an FPGA register-map change and service change are coupled, restore both sides of that contract on the same board. If generator waveform and detector thresholds are release-coupled, restore the known-compatible pair or keep generator disabled.
