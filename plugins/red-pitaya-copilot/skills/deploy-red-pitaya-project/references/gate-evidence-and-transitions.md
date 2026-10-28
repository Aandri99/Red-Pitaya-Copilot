# Deployment gate evidence and transitions

## Rule

Never edit a deployment plan's `gates` or `gate_evidence` by hand. A true gate
without a matching evidence receipt is invalid. Advance one gate at a time with
`scripts/record_deployment_gate.py`.

The tool is offline. It records evidence but does not perform the step, contact
the board, grant authorization or prove that a human statement is genuine.

## Gate order

The generated plan starts with these identity gates satisfied by the normalized
probe report:

1. `host_key_verified`
2. `identity_exact`
3. `os_exact`
4. `model_exact`
5. `role_exact`

Then advance:

1. `dry_run_reviewed`
2. `stage_authorized` before the first staging/network write
3. `stage_hashes_verified`
4. `precommit_live_actions_authorized` before safe-stop or board-side backup
5. `safe_stop_confirmed`
6. `backup_verified`
7. `commit_authorized`
8. `fpga_release_reviewed` for a self-attested release candidate
9. `fpga_load_authorized` when FPGA load is separately authorized
10. `functional_output_test_authorized` when the manifest and user separately
   allow a physical-output test

The transition tool enforces prerequisites. It refuses an in-place edit and an
existing output path.

## Evidence files

Store every evidence file beneath the project root. Examples:

- dry-run review checklist;
- board-side staged file hash report;
- observed safe-stop status response and output-enable register readback;
- backup inventory plus backup file hashes;
- user authorization record;
- post-deployment acceptance report.

The transition records relative path, SHA-256, byte size, UTC time and summary.
Changing the evidence file later makes plan validation fail.

Do not put passwords, private keys, tokens or secrets in evidence.

## Record a non-authorization gate

```text
python scripts/record_deployment_gate.py \
  --plan deployment-plan.json \
  --project-root . \
  --gate dry_run_reviewed \
  --evidence-file evidence/dry-run-review.txt \
  --recorded-at-utc 2026-07-29T12:30:00Z \
  --summary "Operator reviewed exact target, paths, safe stop and rollback." \
  --output deployment-plan.01-dry-run-reviewed.json
```

Use the new output as the input for the next transition.

For `stage_hashes_verified`, the evidence must contain observed hashes from the
board after transfer, not the local expected hash list copied a second time.

For `safe_stop_confirmed`, record both the stop action and independent observed
safe state. An HTTP success alone is insufficient.

For `backup_verified`, record the exact backup root, inventory, hashes and a
readability check.

## Authorization evidence

Use one routine-deployment authorization by default. It is bound to one
immutable plan core, deployment and target and may be reused only for
`stage_authorized`, `precommit_live_actions_authorized` and
`commit_authorized` as their operational prerequisites are completed. Do not
ask the user three times for those routine phases unless the user explicitly
requests stepwise approval or grants only a limited scope.

Never extend routine deployment authorization to FPGA loading or a physical
output test. Those always retain distinct evidence. Never invent
`approver_identity` or `authorization_reference`; copy them from the actual
user/external authorization source.

```json
{
  "authorization_schema_version": 2,
  "gate": "routine_deployment_authorized",
  "result": "authorized",
  "recorded_at_utc": "2026-07-29T12:45:00Z",
  "authorization_scope": "deploy",
  "deployment_id": "example-001",
  "plan_core_sha256": "64-lowercase-hex-characters",
  "target_machine_id": "exact-machine-id-from-the-normalized-probe",
  "ssh_host_key_sha256": "SHA256:exact-verified-fingerprint",
  "approver_identity": "exact approving person/account identity",
  "authorization_reference": "unique message, ticket or signed-record reference",
  "summary": "Approver authorized routine deployment for this exact plan and target."
}
```

Allowed scopes are:

| Evidence gate | Exact `authorization_scope` | May satisfy |
|---|---|---|
| `routine_deployment_authorized` | `deploy` | stage, precommit and commit gates |
| `stage_authorized` | `stage` | stage only |
| `precommit_live_actions_authorized` | `precommit-live-actions` | precommit only |
| `commit_authorized` | `commit` | commit only |
| `fpga_release_reviewed` | `fpga-release-review` | FPGA release review only |
| `fpga_load_authorized` | `fpga-load` | FPGA load only |
| `functional_output_test_authorized` | `physical-output-test` | physical-output test only |

For a gate-specific record, the timestamp in the JSON must exactly equal
`--recorded-at-utc`. For routine-deployment evidence, the JSON timestamp is the
time of the single user authorization and may precede later gate-transition
timestamps; it must never postdate them.

The JSON summary must exactly equal `--summary`. A receipt is rejected if its
deployment ID, immutable plan-core SHA-256, machine ID or SSH fingerprint
differs from the plan. A path, file hash or authorization reference already
used by another authorization gate is rejected except when the exact same
routine-deployment record is reused for the three routine gates. This prevents
cross-plan, cross-target and cross-scope reuse; it does not cryptographically
authenticate the approver.

Populate the four binding fields by reading the latest validated plan:

1. copy top-level `deployment_id`;
2. copy top-level `plan_core_sha256`;
3. copy `target.identity.machine_id`;
4. copy `target.connection.ssh_host_key_sha256`.

Do not recalculate, shorten, normalize or retype those values from memory. Then
record the real approving identity and a unique reference to the approval
source. If any copied binding changes, discard the authorization, generate and
review a new plan, and obtain a new approval.

A stage-only record does not authorize safe-stop, backup or replacement. A
routine-deployment record authorizes the routine transaction, but each gate is
still recorded only after its operational prerequisites succeed. Routine
deployment never authorizes FPGA review, FPGA loading or a physical output
test. Create a separate scoped record for each of those higher-risk actions.

`fpga_release_reviewed` records an independent review of the exact
self-attested release-candidate package. It does not itself authorize loading.
`fpga_load_authorized` is a second receipt and requires the release-review gate.
Package metadata, including any historical `loadable` field, grants no
permission.

## Validate after every transition

```text
python scripts/validate_deployment_plan.py \
  deployment-plan.01-dry-run-reviewed.json \
  --project-root .
```

Before commit, also require:

```text
python scripts/validate_deployment_plan.py \
  <latest-plan>.json \
  --project-root . \
  --require-commit-gates
```

This checks receipts and file hashes in addition to Boolean gate state.
`--project-root` is mandatory with `--require-commit-gates`. Commit validation
re-reads and semantically compares the normalized probe, re-expands the
hash-bound file map, re-runs deployment-ready application validation and
re-runs FPGA package validation. A Boolean-only or detached plan cannot pass.

## Failure behavior

If a step fails:

1. do not record its gate;
2. preserve the failed-step evidence and logs;
3. keep or restore safe stop;
4. record the last plan whose validation still passes;
5. follow the rollback decision tree;
6. do not skip ahead to a later gate.
