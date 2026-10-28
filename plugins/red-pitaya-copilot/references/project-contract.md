# Shared project contract

Every skill in this plugin reads and writes one file at the project root:

`redpitaya-project.json`

Its schema is `schemas/redpitaya-project.schema.json`. Create it with
`scripts/new_redpitaya_project.py` and validate it with
`scripts/validate_redpitaya_project.py`.

## Why the contract exists

Without one shared contract, an application model may assume OS 3 while an FPGA
model packages for OS 2, or a deployment model may install an app on the wrong
board. The manifest turns these assumptions into values that can be checked.

The file is safe to commit. It must never contain credentials.

`application.authentication` records only the application-level access-control
mode. New manifests use `none`. `operator-token` is an explicit opt-in and the
manifest stores the mode, never the token. Deployment receipts and
physical-output authorizations remain separate contracts.

## Required workflow

1. Create the manifest before scaffolding source files.
2. Fill `target.hostname` only when the exact destination is known.
3. Probe the target before a native build or deployment.
4. Update only fields supported by the schema.
5. Run the validator after every manifest edit.
6. Treat validator errors as blockers.
7. Treat warnings as unresolved evidence; explain or resolve each one.
8. Include the validated manifest in the packaged project evidence.
9. Do not add token prompts or bearer-token gating unless
   `application.authentication` is explicitly `operator-token`.

For `application.architecture` equal to `service` or `hybrid`,
`application.service_port` is mandatory and must be 1024-65535. For every
other architecture it must be `null`. The port is a target resource, not a
portable default: inspect the live target with read-only `ss -ltnp`, existing
service units and existing app manifests before calling it deployment-ready.

## Section ownership

| Section | Primary writer | Other readers |
|---|---|---|
| `project` | application skill at project creation | all skills |
| `target` | deployment probe, confirmed by user | application and FPGA skills |
| `application` | application skill | deployment skill |
| `fpga` | FPGA skill | application and deployment skills |
| `deployment` | deployment skill | application and FPGA safety checks |
| `safety` | project owner with help from all skills | all skills |

No skill owns the whole file. Preserve valid values written by another skill.

## Minimum hand-off evidence

A finished project should contain or report:

- the validated manifest;
- exact OS and board probe evidence;
- source revision;
- application package inventory;
- FPGA build/timing evidence when FPGA is enabled;
- register contract when FPGA is enabled;
- deployment plan;
- staged-package hashes;
- health-check and rollback results.

## Tool use

From the plugin root:

```text
python scripts/new_redpitaya_project.py \
  --slug my_instrument \
  --title "My Instrument" \
  --os-release 3.00-57 \
  --model <exact-profiles-f-value> \
  --authentication none \
  --kind hybrid
python scripts/validate_redpitaya_project.py redpitaya-project.json
python scripts/validate_redpitaya_project.py redpitaya-project.json --strict
```

`<exact-profiles-f-value>` is a placeholder, not a literal argument. Obtain it
from the target evidence described in the skills. If the application
architecture is `service` or `hybrid`, also pass `--app-architecture` and an
explicitly verified `--service-port`.

The generator refuses to overwrite an existing manifest. This is deliberate.
Edit a validated manifest consciously rather than regenerating over project
decisions.
