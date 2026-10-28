# Red Pitaya Copilot

This plugin gives Codex one shared, version-pinned workflow for professional
Red Pitaya work:

1. **`$build-red-pitaya-app`** designs, scaffolds and validates launcher apps,
   Nginx/Lua APIs, native controllers, Python/C++ services and hybrid apps.
2. **`$build-red-pitaya-fpga`** designs and verifies RTL, generates register
   bindings, records build evidence and packages model- and OS-specific FPGA
   artifacts.
3. **`$deploy-red-pitaya-project`** probes a board, creates an offline
   deterministic plan, advances receipt-backed safety gates and defines
   transactional deployment and rollback.
4. **`$qualify-red-pitaya-loopback-system`** plans and evaluates controlled
   generator/local-IN1/remote-IN1 qualification with two independently proven
   boards, hash-bound physical evidence, single-use output authorization and
   explicit measured-versus-reconstructed labels.

The suite is intentionally strict. It is written so a less capable agent can
follow explicit evidence, commands, schemas and stop conditions instead of
guessing.

## Supported release profiles

| Contract release | Observed user-space baseline | Python | FPGA package name |
| --- | --- | --- | --- |
| `2.00-35` | Ubuntu 22.04.4, glibc 2.35, systemd 249 | 3.10 | `fpga.bit.bin` |
| `2.07-48` | Ubuntu 22.04.5, glibc 2.35, systemd 249 | 3.10 | `fpga.bit.bin` |
| `3.00-57` | Ubuntu 24.04.4, glibc 2.39, systemd 255 | 3.12 | `fpga.bin` |

OS 2.00-35 and OS 2.07-48 are one verified OS 2.x application and FPGA
packaging family. Do not fork an application or register ABI merely because the
OS 2.x point release changed. Probe small loader/interface differences and keep
the exact release in deployment evidence. OS 1.x and OS 3.x are separate
families. Exact board identity, model and native dependency checks still apply.

## Start here

For a new project:

```text
python scripts/new_redpitaya_project.py \
  --slug my_instrument \
  --title "My Instrument" \
  --os-release 3.00-57 \
  --model <exact-profiles-f-value> \
  --components app-fpga \
  --role acquisition \
  --app-architecture service \
  --service-port <verified-unused-loopback-port> \
  --output <new-project-directory>/redpitaya-project.json
```

Do not copy the example values into a real deployment. Obtain the exact model
from the helper used by the live stock `overlay.sh`: newer OS 2/3 builds may
use `profiles -f`, while the verified OS 2.00-35 loader uses `monitor -f`.
Corroborate it with `monitor -n`, `monitor -i`, the physical inventory and the
FPGA device, and verify the service port on the target.

The generated `redpitaya-project.json` is the only cross-skill contract. Never
create separate app, FPGA and deployment manifests.

## Required workflow

```text
Board/image evidence
        |
        v
redpitaya-project.json
        |
        +--> App skill --> validated app/service
        |
        +--> FPGA skill --> evidence-bound release package
        |
        +--> Deploy skill --> probe-bound plan --> evidence gates --> commit
        |
        `--> Qualification skill --> two-board plan --> single-use run --> report
```

Use the application skill for software behavior, the FPGA skill for deterministic
logic and register contracts, the deployment skill for every live write, and
the qualification skill for controlled multi-board measurements after both
deployments are independently authorized.
Read [references/workflow-map.md](references/workflow-map.md) for the exact
ownership and handoff rules.

## Safety boundary

The local tools do not connect to a board, load an FPGA, remount storage,
install a service or enable an output. They validate files and create plans.
A live change still requires:

- independently verified SSH host identity;
- exact OS, model and role agreement;
- warning-free read-only probe evidence;
- safe-stop confirmation;
- verified staging hashes and board backup;
- explicit commit authorization;
- separate FPGA-load authorization;
- separate physical-output-test authorization when applicable;
- acceptance testing and a recoverable rollback record.

Synthetic FPGA fixtures are labelled `test-fixture`, are marked
`DO_NOT_LOAD.txt`, and are rejected by the deployment planner.

## Validate the complete plugin

From this directory:

```text
python scripts/run_suite_checks.py
```

The runner checks every JSON file and relative Markdown link, parses every
Python and JavaScript source, then executes:

- shared profile/manifest tests;
- the 12-case app matrix (six backends across both OS releases) plus
  deterministic app-package integrity, tamper and reproducibility tests;
- the FPGA contract, evidence and deterministic-package tests;
- deployment probe, plan, receipt and mismatch tests;
- two-board qualification provenance, single-use authorization, measurement
  integrity and report tests.

All checks are offline. Passing them proves the plugin's local contracts and
fixtures are internally consistent; it does not replace Vivado implementation,
target-image ABI checks or controlled hardware acceptance.

## Important references

- [Project contract](references/project-contract.md)
- [Compatibility matrix](references/compatibility-matrix.md)
- [Cross-skill workflow](references/workflow-map.md)
- [Application skill](skills/build-red-pitaya-app/SKILL.md)
- [FPGA skill](skills/build-red-pitaya-fpga/SKILL.md)
- [Deployment skill](skills/deploy-red-pitaya-project/SKILL.md)
- [Loopback qualification skill](skills/qualify-red-pitaya-loopback-system/SKILL.md)
