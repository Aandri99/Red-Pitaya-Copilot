# Failure-state handoff

Use this structure whenever work stops before `COMPLETE`.

## Required status

```text
Deployment ID:
State:
Last completed step:
First failed step:
Failure timestamp:

Target:
  Intended role:
  Verified hostname/address:
  SSH host-key fingerprint:
  Machine ID:
  Serial/MAC:
  OS release:
  Model:

Safety:
  Output disable requested:
  Output disable independently verified:
  External system energized:
  Human/physical verification still required:

Storage:
  Original /opt/redpitaya target/source/fstype/options:
  Current /opt/redpitaya target/source/fstype/options:
  Mount restoration verified:

Artifacts:
  Local plan and hash:
  Stage path:
  Stage verification:
  Backup path:
  Backup verification:

Live changes:
  Replaced paths:
  Newly created paths:
  Unchanged planned paths:
  Paths with uncertain state:

Runtime:
  Nginx config test/state:
  Services stopped/running/failed:
  FPGA prior/current signature:

Failure evidence:
  Exact command/request:
  Exit/status code:
  Unmodified stdout/stderr/response:

Safe next action:
  [retry stage | restore mount | roll back | physical check | await authority]
```

## State selection

- `FAILED_SAFE`: failure occurred and outputs are confirmed disabled; live state may or may not be complete.
- `ROLLBACK_REQUIRED`: commit began and the new state failed a critical acceptance test.
- `ROLLED_BACK`: prior payload/configuration was restored but still requires/has completed acceptance.
- Never use `COMPLETE_WITH_WARNINGS` for a failed safe-stop, wrong FPGA signature, unknown mount state or identity mismatch.

## Handoff quality rules

- Link or name exact evidence files.
- Preserve error output verbatim; add interpretation separately.
- State uncertainty explicitly.
- Do not advise a reboot until the implications for mount state, service startup and output behavior are known.
- Do not instruct the next operator to rerun the entire deployment blindly.
- Give one safest next action and its prerequisite.
- If physical verification is needed, say what must be observed and where.

## Common recovery decisions

### Stage failed, commit not started

Keep live system unchanged. Verify identity and free space, remove or resume only the exact staging directory, then generate a new stage result.

### Backup failed, commit not started

Do not commit. Preserve safe-stop state if already asserted. Fix backup destination/space and repeat backup.

### `/opt/redpitaya` left writable

Keep outputs disabled. Verify exact mount target, run `sync`, restore original mount state and verify. If exact target cannot be proven, stop for manual recovery.

### Nginx test failed

Do not reload. Restore the prior fragment/app files from verified backup, retest prior configuration, then restore mount state.

### New FPGA signature is wrong

Stop dependent services and keep output inhibit asserted. Restore the prior verified artifact/overlay if available and authorized; otherwise leave FPGA-dependent services stopped.

### SSH connection lost during commit

Do not assume success or failure. Reconnect only after host-key/identity verification, inspect deployment record, mount state, live hashes, services and safe-stop readback, then select resume or rollback.
