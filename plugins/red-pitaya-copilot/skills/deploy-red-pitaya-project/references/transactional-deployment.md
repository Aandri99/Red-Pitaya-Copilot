# Transactional deployment

## Contents

1. Definitions and invariants
2. Preflight
3. Staging
4. Backup
5. `/opt/redpitaya` VFAT handling
6. Commit
7. Cleanup
8. Prohibited shortcuts

## 1. Definitions and invariants

Use one validated plan and one unique deployment ID. Derive:

```text
stage root  = /tmp/redpitaya-deploy/<deployment-id>
backup root = /root/redpitaya-backups/<project-slug>/<deployment-id>
```

Before using a deployment ID in any command, require:

```text
^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$
```

Apply this destination policy after expanding every directory mapping:

| Component | Only permitted live destination |
|---|---|
| `app` | Below exact `<app_root>/<application.slug>/` |
| `nginx` | Exact app-root `nginx.conf` or below its `nginx/` directory |
| `service` | Exact `/etc/systemd/system/<application.service_name>` |
| `fpga` | Exact app-root `<fpga.artifact_name>` |
| `device-tree` | Exact app-root `<fpga.device_tree_name>` |
| `fpga-profile` | Exact verified OS profile root `<target.model>/<application.slug>/<fpga.artifact_name>` |
| `device-tree-profile` | Exact verified OS profile root `<target.model>/<application.slug>/<fpga.device_tree_name>` |
| `configuration` | App root or `/etc/redpitaya/<slug>/`; register contract is exact app-root `register-contract.json` |
| `other` | Planning-only project-scoped `/usr/local` location; never committable |

Reject a destination unless its original string is a canonical absolute ASCII
path. Reject whitespace, controls, quotes, backticks, substitutions,
metacharacters, wildcards, `.`/`..`, repeated separators and broad roots. Never
write `/opt/redpitaya/bin/`, `/opt/redpitaya/sbin/`, `overlay.sh`, `fpga.sh`,
SSH service units, Nginx service units or an undeclared systemd unit.

Maintain these invariants:

- Physical outputs stay disabled from pre-commit through acceptance.
- `stage_authorized` precedes every staging/network write.
- `precommit_live_actions_authorized` precedes safe-stop changes and board-side
  backup writes. Both gates and `commit_authorized` may cite the same valid
  routine-deployment authorization; do not require three user confirmations by
  default.
- Stage and backup paths never equal `/`, `/tmp`, `/root`, `/opt`, or `/opt/redpitaya`.
- Only paths listed by the plan may change.
- `/opt/redpitaya` returns to its original `ro`/`rw` state.
- Backup creation precedes live replacement.
- Backups, retired releases and staging copies remain outside
  `/opt/redpitaya/www/apps` and every other launcher-scanned application root.
- Stage hashes equal plan hashes before commit.
- Live hashes equal plan hashes after commit.
- For `overlay.sh` profiles, app-root and permanent-profile FPGA/DTBO hashes
  are identical before load, and the effective `fpga.conf` path resolves to
  that same qualified hash.
- Rollback metadata identifies files that were absent before deployment.

## 2. Preflight

Immediately before staging:

1. Re-run immutable identity checks.
2. Re-read `/opt/redpitaya` target, source, filesystem type and options.
3. Confirm no other deployment holds the project’s deployment lock.
4. Inspect relevant app/service/FPGA ownership and active processes.
5. Confirm space for stage, backup and temporary archives.
6. Confirm current time or record that time is not synchronized. Use the local deployment ID as the primary correlation identifier.
7. Validate the local plan again against the unchanged project files.

Never use an unresolved environment variable or wildcard as a destructive target.
Require the canonical `plan_core_sha256` and all authorization bindings to
validate before acting. The validator re-reads the normalized probe and exact
file map; copied target fields are never independent authority.

## 3. Staging

Staging must not require remounting `/opt/redpitaya`.

1. Require a valid `stage_authorized` receipt for this exact deployment ID,
   immutable plan-core hash, machine ID and SSH fingerprint.
   Routine-deployment authorization is the default evidence source; use a
   stage-only record only when the user limited the scope.
2. Validate the resolved parent is exact `/tmp/redpitaya-deploy`. The shared
   manifest must declare `/tmp/redpitaya-deploy/{deployment_id}`; the planner
   replaces that placeholder with the reviewed deployment ID.
3. Refuse an existing deployment-ID directory unless it contains the same plan identity and the user explicitly chose resume.
4. Create the unique directory with root-only or deployment-user-only permissions.
5. Upload the plan, project manifest, file map and payload under separate subdirectories.
6. Preserve relative payload paths exactly; never flatten two source files onto one destination.
7. Generate an on-board hash listing from the staged bytes.
8. Compare:
   - File count.
   - Relative path.
   - Byte size.
   - SHA-256.
9. Run non-mutating syntax/format checks on staged files.
10. Mark stage verified only after a complete exact match.

Because staging is isolated beneath `/tmp` and does not touch live
configuration or hardware, a known active output does not itself block the
stage transfer. Record that state and require verified safe-stop immediately
after the precommit gate and before backup or replacement. An unknown output
state remains blocking.

Do not copy directly from the workstation to a live app path. Network interruption must leave only an incomplete staging tree, not a half-updated live application.

## 4. Backup

Back up each plan destination before replacing it.

Do not request safe-stop or create this board-side backup until the
deployment-bound `precommit_live_actions_authorized` receipt validates. When a
routine-deployment authorization is already bound to the plan, reuse it without
prompting the user again.

For every destination record:

- `existed_before`: true or false.
- Original type: file, directory, symlink or absent.
- Owner, group and mode where supported.
- Size, mtime and SHA-256 for regular files.
- Link target for a permitted symlink.
- Backup payload location.

Use the manifest template
`/root/redpitaya-backups/<project-slug>/{deployment_id}`. Materialize only the
reviewed deployment ID, then use that unique project-scoped backup root. Backup
the effective configuration involved in activation, including:

- Existing app directory/files.
- Existing Nginx app fragment or effective include source.
- Existing systemd unit and drop-ins.
- Existing FPGA artifact, device-tree overlay, loader configuration and current signature information.
- Both the app-packaged FPGA/DTBO copies and the permanent launcher-profile
  copies when the selected OS profile declares that layout.
- Current enabled/active service state.
- Original `/opt/redpitaya` mount record.

After copying, hash the backup independently. A backup is not verified until its manifest describes every planned destination and every copied regular file hash matches.

If backup creation fails, do not begin live replacement.

Never back up an installed app to a sibling such as
`/opt/redpitaya/www/apps/<slug>.backup.<timestamp>`. Bazaar can discover the
copied `info/info.json` and render a duplicate application tile. A rollback
payload is operational evidence, not an installed application; keep it under
the exact project-scoped backup root.

## 5. `/opt/redpitaya` VFAT handling

The analyzed OS 2.07-48 and OS 3.00-57 images use a Red Pitaya application partition mounted at `/opt/redpitaya`, commonly VFAT and read-only. The live observation controls.

### Guard before remount

Read:

```text
findmnt -T /opt/redpitaya -o TARGET,SOURCE,FSTYPE,OPTIONS
```

Require all of:

- Resolved target is exact `/opt/redpitaya`.
- The destination being installed is beneath `/opt/redpitaya/`.
- Backup is verified.
- Stage is verified.
- Safe stop is confirmed.
- Commit is authorized.

Record whether the original options contain `ro` or `rw`.

### Remount

If originally `ro`, request only:

```text
mount -o remount,rw /opt/redpitaya
```

Immediately re-run `findmnt` and require exact `/opt/redpitaya` with `rw`. Do not specify, infer or operate on the underlying block device. Do not remount `/`.

If already `rw`, do not perform a redundant remount; record the unexpected or intentional state.

### Guaranteed restoration

Install through a cleanup structure that runs after success, command failure, interruption and rollback attempt. After file changes:

1. Run `sync`.
2. If originally `ro`, run:

   ```text
   mount -o remount,ro /opt/redpitaya
   ```

3. Verify exact mount target and `ro`.
4. If restoration fails, keep outputs disabled, stop dependent services and hand off as `FAILED_SAFE`. Do not claim completion.

VFAT does not provide normal Unix ownership/permission behavior. Treat executable launchers and ownership semantics according to the live mount and launcher mechanism, not assumptions from an ext4 workstation.

## 6. Commit

Use this order:

1. Validate `precommit_live_actions_authorized`; reuse the existing
   routine-deployment evidence when present.
2. Confirm safe stop physically or through independent readback.
3. Verify backup.
4. Validate final `commit_authorized`; reuse the existing routine-deployment
   evidence after backup verification instead of requesting another approval.
5. Remount exact `/opt/redpitaya` only if required.
6. Create required destination directories with narrowly scoped paths.
7. Install each staged regular file to an adjacent temporary file on the same destination filesystem when possible.
8. Hash the temporary file.
9. Rename it onto the exact destination where filesystem semantics support it.
10. For a directory release, prefer a versioned release directory plus a controlled switch only when the live framework supports links on that filesystem. Do not assume symlink support on VFAT.
11. Run `sync`.
12. Verify every live path, size and hash.
13. Resolve the loader configuration and verify its effective bitstream and
    DTBO hashes equal the app copies and qualified release package. Treat a
    stale, missing, or differently hashed permanent profile as a failed commit.
14. Restore mount state.
15. Activate configurations in dependency order.

If a file changed between backup and replacement, stop. Another actor modified the target; do not overwrite the concurrent change.

### Activation order

Use the plan’s components:

1. Static/app payload.
2. Service executable/configuration.
3. Systemd unit validation and daemon reload.
4. Nginx configuration validation and reload.
5. FPGA load, only if separately authorized.
6. Dependent service start/restart.
7. Acceptance tests.

An FPGA-dependent service must not start against an unknown register map. Verify FPGA magic/build/register-map version first.

## 7. Cleanup

After acceptance:

- Preserve the verified backup according to retention policy.
- Preserve the plan, probe report, stage hash result, live hash result and acceptance report.
- Remove only the exact staging directory after resolving and comparing it with the plan’s `stage_root`.
- Do not delete a backup as part of normal staging cleanup.
- Release the deployment lock.
- Record final service, FPGA and output state.

After failure:

- Preserve evidence.
- Keep safe stop asserted.
- Restore mount state before optional stage cleanup.
- Do not remove staging or backup until recovery no longer needs them.

## 8. Prohibited shortcuts

Never:

- Copy over live paths before backup.
- Use `scp -r` directly into `/opt/redpitaya/www/apps/<slug>` as the transaction.
- Use `chmod -R`, `chown -R`, broad globs or recursive delete on `/opt/redpitaya`.
- Use `rm -rf` with an unset/computed target.
- Mount or remount a guessed `/dev/mmcblk*` device.
- Load an FPGA bitstream to “see if it works.”
- Reload Nginx without a successful configuration test.
- restart all services or reboot as a substitute for identifying the affected unit.
- Leave a board writable or outputs enabled after a failed verification.
- Install any destination that is not derivable from its manifest component.
- Commit a `component: other` entry.
- Replace `/opt/redpitaya/sbin/overlay.sh`, another Red Pitaya tool/loader, or
  an undeclared systemd unit.
