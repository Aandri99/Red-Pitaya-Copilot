# Beta test report

Date: October 28, 2026. Environment: Windows, Python 3.13, Node.js, and the locally installed Codex CLI.

Tested plugin source revision: `30b8fc547b7ac82af86a59ee06c89b57ab14e6a8`.

## GitHub installation — passed

A fresh clone of the public repository succeeded. These commands also succeeded:

```sh
codex plugin marketplace add Aandri99/Red-Pitaya-Copilot --ref main --json
codex plugin add red-pitaya-copilot@red-pitaya-beta --json
codex plugin list --marketplace red-pitaya-beta --json
```

Codex reported the beta as installed and enabled. All 126 installed plugin files matched the fresh GitHub clone by SHA-256. All five skill folders were present. This beta installation uses a separate marketplace from the pre-existing personal installation.

## Offline validation — passed

Run from `plugins/red-pitaya-copilot`:

```sh
python scripts/run_suite_checks.py
```

| Suite | Passed | Failed |
| --- | ---: | ---: |
| Shared profile and project contracts | 16 | 0 |
| Application matrix and package checks | 104 | 0 |
| FPGA evidence and package tests | 14 | 0 |
| Deployment plans and authorization gates | 26 | 0 |
| Two-board qualification logic | 32 | 0 |
| Single-board qualification logic | 24 | 0 |
| **Total** | **216** | **0** |

JSON, Python, and JavaScript syntax validation and 106 relative documentation links also passed. The runner exited successfully and reported `ALL OFFLINE SUITE CHECKS PASSED`.

An earlier restricted run could not use Windows loopback sockets and reported six WebSocket failures with WinError 10013. All six passed in this complete rerun with local networking available. No plugin source changes were needed.

## Scope and remaining tests

This establishes that GitHub marketplace installation and the bundled offline checks work in the tested Windows environment. It does not establish compatibility with every desktop version or account.

No Red Pitaya board was contacted or modified. Live deployment, FPGA implementation and loading, physical outputs, ADC/DAC behavior, and real single-board/two-board measurements were not tested. A new-chat prompt walkthrough and the quality of generated solutions also remain unverified.
