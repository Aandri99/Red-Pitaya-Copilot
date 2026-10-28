---
name: deploy-red-pitaya-project
description: Plan, inspect, stage, deploy, verify, coordinate and roll back Red Pitaya projects without guessing the target board. Use for SSH deployment, `/opt/redpitaya` application installation, OS 2.x `fpga.bit.bin` artifacts, OS 3.x `fpga.bin` artifacts, Nginx routes, systemd services, multi-board generator/acquisition systems, board migration, failed deployment recovery, or any request that may modify a live STEMlab or Red Pitaya. Also use to produce a dry-run deployment plan or diagnose why a deployed app, service, FPGA overlay or web route is not active.
---

# Deploy Red Pitaya Project

Treat deployment as a gated transaction. Establish identity from read-only evidence, build a local machine-checkable plan, stage without touching live files, commit only after explicit authorization, verify the safe state, and retain a tested rollback path.

## Mandatory completion report

Before every final response, read and follow
[../../references/completion-report.md](../../references/completion-report.md).
Apply it even for read-only, diagnostic, partial or blocked work.

## Route required references

Read the listed reference before performing the corresponding phase:

- Identity, SSH and probe collection: [references/probe-and-identity.md](references/probe-and-identity.md)
- Transactional staging, VFAT remount and commit: [references/transactional-deployment.md](references/transactional-deployment.md)
- Target OS 2.00-35: [references/os-2.00-35.md](references/os-2.00-35.md)
- Target OS 2.07-48: [references/os-2.07-48.md](references/os-2.07-48.md)
- Target OS 3.00-57: [references/os-3.00-57.md](references/os-3.00-57.md)
- Nginx or systemd changes: [references/nginx-and-services.md](references/nginx-and-services.md)
- Acceptance tests, rollback or recovery: [references/acceptance-and-rollback.md](references/acceptance-and-rollback.md)
- Gate receipts and authorization transitions: [references/gate-evidence-and-transitions.md](references/gate-evidence-and-transitions.md)
- Two or more boards: [references/multi-board-coordination.md](references/multi-board-coordination.md)
- An incomplete or failed operation: [references/failure-state-handoff.md](references/failure-state-handoff.md)
- Before reading or changing `redpitaya-project.json`: [../../references/project-contract.md](../../references/project-contract.md) and [../../schemas/redpitaya-project.schema.json](../../schemas/redpitaya-project.schema.json)
- For OS/model/build compatibility: [../../references/compatibility-matrix.md](../../references/compatibility-matrix.md), [../../profiles/os-2.00-35.json](../../profiles/os-2.00-35.json), [../../profiles/os-2.07-48.json](../../profiles/os-2.07-48.json), [../../profiles/os-3.00-57.json](../../profiles/os-3.00-57.json), and [../../profiles/board-models.json](../../profiles/board-models.json)
- For the application/FPGA/deployment permission boundary and handoff packet: [../../references/workflow-map.md](../../references/workflow-map.md)

Do not rely on memory for version-specific artifact names or loader behavior. Read the matching OS reference after the probe identifies the release.

## Non-negotiable rules

1. Never infer board identity from a hostname, mDNS suffix, IP address, enclosure position or cable alone.
2. Never accept a changed SSH host key automatically. Stop, identify the physical cause, and obtain the expected fingerprint through an independent trusted channel.
3. Never put a password, token, private key, Arduino Cloud secret or SSH command-line password in source, a plan, a log or a plugin asset.
4. Use keys, an SSH agent or an interactive prompt. Keep strict host-key checking enabled.
5. Perform read-only inspection before every live write, including repeat deployments.
6. Match `redpitaya-project.json` `schema_version: 1`, `target.os_release` and `target.model` to observed evidence exactly. Stop on mismatch.
7. Verify the board role when a system has multiple boards. A generator artifact must not be installed on the acquisition board.
8. Never load an FPGA artifact built for a different model, OS packaging profile or Vivado/project target.
9. Stop or inhibit physical outputs before replacing applications, services or FPGA logic.
10. Stage and verify hashes before changing live files.
11. Back up exact live paths before replacement. Never call a copied local source tree a board backup.
12. Remount only the exact `/opt/redpitaya` mount. Never remount `/`, a parent device, or an inferred block device.
13. Restore `/opt/redpitaya` to its observed original state, including read-only state, even after failure.
14. Never report success from upload completion alone. Success requires acceptance tests and a verified safe-stop action.
15. Do not connect to or modify a board merely to create or validate a deployment plan.
16. FPGA deployment sources must come from one structurally validated
    `classification: release-candidate` package. Treat its offline provenance
    as self-attested: package metadata never grants load permission. Reject
    `test-fixture`, `DO_NOT_LOAD.txt`, loose binaries and missing register
    contracts. Require separate `fpga_release_reviewed` and
    `fpga_load_authorized` receipts before a load.
17. A gate is true only when its hash-bound evidence receipt validates. Never
    flip plan gate booleans manually.
18. Require one exact application bundle mapping rooted at
    `/opt/redpitaya/www/apps/<manifest-slug>` when `application.enabled` is
    true. Its embedded manifest must equal the deployment manifest and its
    deployment-ready validation provenance must pass with no warning hidden.
19. Reject whitespace, control characters, shell metacharacters, broad
    destinations, protected Red Pitaya loader/tool paths and undeclared service
    units. `component: other` is never committable.
20. `fpga_manager=operating` proves configuration completion only. Perform the
    first custom-register identity read in an expendable helper. SIGBUS, AXI
    external abort, timeout, or identity mismatch requires dependent-service
    stop and immediate known-good FPGA rollback.
21. After a service replacement, verify the actual loopback listener and
    proxied HTTP/WebSocket route. Include an immediate restart check so
    TIME_WAIT/orphaned-worker failures cannot masquerade as an active service.
    Poll readiness to a short bounded deadline; do not require the listener in
    the same instant that systemd changes the unit to active.
22. Keep deployment backups, retired releases and staging copies outside every
    launcher-scanned application root. Never create `*.backup.*` siblings under
    `/opt/redpitaya/www/apps`; a copied manifest can become a duplicate Bazaar
    tile. When launcher visibility is required, acceptance must prove the exact
    slug occurs once, its tile opens the intended route, and tile-less helper
    services do not appear.

## Required inputs

Collect or state that each item is unavailable:

- Local project root and `redpitaya-project.json`.
- Expected board role, such as `generator`, `acquisition`, `controller` or `standalone`.
- Intended hostname/address and SSH username.
- Independently verified SSH host-key fingerprint.
- Read-only probe record and normalized probe report.
- Explicit local-to-remote file map.
- Safe output state and safe-stop method.
- User authorization scope: inspect only, stage only, routine deploy, or deploy and exercise outputs.

Do not expand authorization. "Inspect" does not authorize staging; "stage"
does not authorize commit or FPGA load. By default, one explicit
plan-bound `routine deploy` authorization covers staging, safe-stop, backup,
live replacement, activation and safe-first acceptance for the reviewed plan.
Do not ask for separate stage, precommit and commit confirmations unless the
user explicitly requests stepwise approval or limits the scope. Routine deploy
never authorizes an FPGA load or energizing a physical output; those retain
separate authorization.

Validate the shared contract before probe comparison or plan generation:

```text
python ../../scripts/validate_redpitaya_project.py redpitaya-project.json --strict
```

Preserve exact schema field and enum values. Read the live stock `overlay.sh`
and copy the exact non-empty output of the helper it uses to `target.model`;
the verified OS 2.00-35 loader uses `monitor -f`, while newer releases may use
`profiles -f`. Do not translate a marketing name into a model code. Use
`monitor -n`, `monitor -i`, the FPGA directory and physical inventory as
separate corroboration.

## Deployment state machine

Use these exact states and do not skip forward:

```text
UNIDENTIFIED
  -> PROBED
  -> IDENTITY_MATCHED
  -> PLAN_VALIDATED
  -> STAGE_AUTHORIZED
  -> STAGED
  -> STAGE_VERIFIED
  -> PRECOMMIT_LIVE_ACTIONS_AUTHORIZED
  -> SAFE_STOP_CONFIRMED
  -> BACKED_UP
  -> COMMIT_AUTHORIZED
  -> COMMITTED
  -> FPGA_RELEASE_REVIEWED        (only when FPGA load is in scope)
  -> FPGA_LOAD_AUTHORIZED         (separate authorization)
  -> FPGA_LOADED                  (only after both FPGA gates)
  -> ACCEPTANCE_PASSED
  -> COMPLETE

Any state -> FAILED_SAFE
COMMITTED or later -> ROLLBACK_REQUIRED -> ROLLED_BACK -> ACCEPTANCE_PASSED
```

Skip the three FPGA-only states when `fpga.enabled` is false or live loading is
not authorized; installing a package file is not permission to load it.

Record the current state, deployment ID, board identity, original mount state, staging path, backup path and last completed step after every transition.

## Phase 1: Probe without changing state

Follow [references/probe-and-identity.md](references/probe-and-identity.md).

1. Verify the SSH host key before login.
2. Collect hostname, machine ID, serial/model evidence, MAC addresses, OS release, kernel, Red Pitaya version files, mount details, loader script behavior, relevant services, processes, ports and currently loaded FPGA signature if available.
3. Record the exact SSH username, real UTC capture time and probe source. Do
   not leave audit fields blank.
4. Record the intended board role separately from physical evidence.
5. Save the observations as a local JSON probe record. Do not include secrets.
6. Normalize and compare it:

```text
python scripts/analyze_probe.py probe.json \
  --manifest redpitaya-project.json \
  --expected-role acquisition \
  --output probe-report.json
```

Exit code zero means the analyzer found no blocking mismatch; it is not
deployment authorization. Resolve every warning before plan generation. The
planner deliberately rejects warning-bearing probe reports.

## Phase 2: Generate a deterministic plan

Create an explicit file map. Every entry names a local source, absolute remote destination, component and file mode. Avoid globs and symlinks.

```json
{
  "schema_version": 1,
  "files": [
    {
      "source": "dist/my_app",
      "destination": "/opt/redpitaya/www/apps/my_app",
      "component": "app",
      "mode": "0644"
    }
  ]
}
```

Generate a plan using a stable deployment ID containing only letters, digits, dots, underscores and hyphens:

```text
python scripts/generate_deployment_plan.py \
  --manifest redpitaya-project.json \
  --probe-report probe-report.json \
  --project-root . \
  --file-map deploy-files.json \
  --deployment-id my_app-20260729T120000Z \
  --output deployment-plan.json
```

The generator expands directories into sorted file records, rejects symlinks and secret-like files, calculates SHA-256 hashes, encodes OS-aware FPGA rules and emits semantic steps. It does not run SSH or touch a board.

When `application.enabled` is true, map the complete application directory once
as `component: app` to the exact root
`/opt/redpitaya/www/apps/<application.slug>`. Do not map a loose subset. The
planner requires a manifest inside that directory equal to the project
manifest, runs the app validator in `--deployment-ready` mode, and records
hash-bound bundle inventory and validator provenance. Native-build warnings
remain blocking.

When `fpga.enabled` is true, the application bundle supplies the app-root
bitstream and device-tree copies. If no application bundle is part of the plan,
map those copies explicitly as `fpga` and `device-tree`. Always map
`register-contract.json` as `configuration` from the validated package.

For an OS profile that declares `fpga_profile_root_template` and uses
`overlay.sh`, also map the same validated package artifacts as `fpga-profile`
and `device-tree-profile` to
`/opt/redpitaya/fpga/<target.model>/<application.slug>/`. The planner requires
the app and permanent-profile SHA-256 values to match. A valid app-root copy is
not sufficient: a stale permanent profile makes the launcher load the wrong
image. All FPGA provenance sources must come from one package directory created
by `$build-red-pitaya-fpga`. The planner reruns that package validator and
rejects loose files, test fixtures, missing evidence, missing profile copies,
and hash disagreement. A structurally valid release candidate remains blocked
from FPGA loading until an independent, deployment-bound
`fpga_release_reviewed` receipt and a separate `fpga_load_authorized` receipt
exist.

Validate locally:

```text
python scripts/validate_deployment_plan.py deployment-plan.json --project-root .
```

Resolve every error. Explain warnings. Review the complete destination list, target identity, safe-stop contract, backup destination, original mount state and rollback steps with the user before live actions.

Do not edit `gates` booleans manually. Read
[references/gate-evidence-and-transitions.md](references/gate-evidence-and-transitions.md)
and advance each gate with `scripts/record_deployment_gate.py`; it binds the
transition to a local evidence file and refuses in-place plan edits.

## Phase 3: Dry-run and stage

A dry-run consists of inspection and local validation, not a fake successful deployment.

1. Re-probe immediately before staging and compare immutable identity fields.
2. Record `dry_run_reviewed`, then obtain one deployment-bound authorization.
   Use routine-deployment scope by default; use stage-only scope only when the
   user explicitly limits authorization or requests stepwise confirmation.
   Record `stage_authorized` from that evidence before the first target write.
3. Confirm free space in the writable staging filesystem and backup filesystem.
4. Create only the unique staging directory from the plan. The shared contract
   materializes `/tmp/redpitaya-deploy/{deployment_id}` as
   `/tmp/redpitaya-deploy/<deployment-id>`.
5. Use the project-scoped backup template
   `/root/redpitaya-backups/<project-slug>/{deployment_id}` and materialize the
   same reviewed deployment ID.
6. Transfer files to staging without `sudo`, remounting or replacing live files.
7. Hash every staged file on the board and compare it to the plan.
8. Validate parsable files in staging: JSON, Python, JavaScript, shell, Nginx fragments and systemd units as applicable.
9. Mark `STAGE_VERIFIED` only if file count, relative paths, sizes and SHA-256 hashes all match.

Stop on partial upload, unexpected extra files, insufficient space, identity
drift or a hash mismatch. A known active output does not by itself block an
isolated `/tmp` staging transfer; record it and require verified safe-stop
before backup or commit. Remove only the exact unique staging directory after
verifying its resolved path.

## Phase 4: Commit

Commit requires explicit user authorization after the validated plan and dry-run results are known. Reuse the routine-deployment authorization by default; do not prompt again for precommit or commit when that exact evidence remains valid.

1. Reconfirm board identity, role, target OS/model and SSH host key.
2. Record `precommit_live_actions_authorized`. When the original evidence has
   routine-deployment scope, reuse it without another user confirmation. A
   stage-only authorization is insufficient. Safe-stop changes and board-side
   backup writes must not occur before this gate.
3. Invoke and verify the safe-stop mechanism. Confirm actual output-disable evidence, not only an HTTP 200 response.
4. Create a timestamped backup of every live destination and relevant configuration. Write a manifest and hashes beside the backup.
5. Record whether each destination existed before deployment. Rollback must delete newly introduced files and restore replaced files.
6. Record `commit_authorized` only after backup verification. Reuse valid
   routine-deployment evidence without another prompt; obtain a new approval
   only when the earlier scope was limited or the plan/target binding changed.
7. If and only if a destination is under `/opt/redpitaya` and that exact mount is read-only, remount `/opt/redpitaya` read-write as described in [references/transactional-deployment.md](references/transactional-deployment.md).
8. Install from the verified staging tree using exact paths and modes. Do not stream unverified content directly into a live destination.
9. `sync`, verify live hashes, then restore `/opt/redpitaya` to its original mount state in a guaranteed cleanup path.
10. Validate and reload Nginx only if its effective configuration changed.
11. Validate, daemon-reload, enable/start or restart systemd units only as specified by the plan.
12. Load FPGA logic only in its own guarded step after OS/model/artifact checks,
    safe-stop confirmation, independent `fpga_release_reviewed` evidence and a
    separately scoped `fpga_load_authorized` receipt.
13. On any failure, stop dependent services, maintain output inhibit, restore mount state and enter `FAILED_SAFE` or `ROLLBACK_REQUIRED`.

Never leave `/opt/redpitaya` writable as a convenience.

## Phase 5: Acceptance

Follow [references/acceptance-and-rollback.md](references/acceptance-and-rollback.md). Test in increasing risk order:

1. Identity and mount-state invariants.
2. Static file hashes and effective configuration.
3. Nginx and service health.
4. Status/read-only endpoints.
5. Read-only non-mutation: repeated status/version/health/capture calls preserve
   overlay identity, generator state, persistent counters and unrelated
   channels.
6. Safe-stop endpoint or command.
7. FPGA signature, register-map version and default output-disabled state.
8. Functional loopback or signal tests only if authorized.
9. Long-running acquisition integrity when applicable: monotonic sequence, epoch, FIFO level/high-water, dropped count, service health and coverage.

If a critical test fails, do not leave the new release running. Roll back or keep the system explicitly disabled and report `ROLLBACK_REQUIRED`.

## Multi-board systems

Follow [references/multi-board-coordination.md](references/multi-board-coordination.md). Maintain a separate probe, plan, staging path, backup and state record per board. Deploy the consumer/acquisition board before the generator when practical, keep all outputs disabled, verify each independently, then enable the signal path in a final coordinated step. Never parallelize live FPGA loads or output-enabling actions.

## Failure handoff

Follow [references/failure-state-handoff.md](references/failure-state-handoff.md). Report:

- Current state and last successful transition.
- Exact verified board identity, role, OS and model.
- Whether physical outputs are confirmed disabled.
- Original and current `/opt/redpitaya` mount states.
- Stage and backup paths with checksums.
- Changed, unchanged and uncertain live paths.
- Service/Nginx/FPGA state.
- First failing command or acceptance test and its unmodified output.
- Exact safe next action: retry stage, complete cleanup, roll back or await user/physical verification.

Never say only "deployment failed." Make the next operator able to recover without guessing.

Preserve the cross-skill handoff packet from [../../references/workflow-map.md](../../references/workflow-map.md): the validated shared manifest, source/artifact paths, exact OS/model evidence, validation results, unresolved warnings, safe-stop contract and an explicit list of actions not performed. Do not redesign application or FPGA behavior while the target filesystem is writable; return that work to the owning skill and generate a new deployment plan afterward.

## Test the bundled tooling

Run:

```text
python scripts/test_deployment_tools.py
```

The tests are local and create temporary fixtures. They do not use a network connection.
