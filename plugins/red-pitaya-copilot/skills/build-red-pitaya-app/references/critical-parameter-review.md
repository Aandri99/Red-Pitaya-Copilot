# Critical Parameter Review

Use this contract for an instrument app that lets an operator change signal-processing, acquisition, timing, calibration, output, or safety-relevant settings.

## Required design

1. Store the catalog at `protocols/critical-parameters.json` with `schema_version: 1`, the exact app slug in `instrument`, and `review_required_before_apply: true`.
2. Describe every critical runtime and fixed parameter. Include its units, range or fixed value, plain-language purpose, change behavior, tradeoff when relevant, and safety class.
3. Keep fixed build/deployment facts visible. At minimum explain the target OS/model, FPGA/sample clock, input/output ownership, output-default policy, and which changes require rebuild, redeploy, or authorization.
4. Expose the catalog through a read-only exact `/api/parameters` route when the app has `/api/configure`. Prove repeated reads do not alter control registers, counters, configuration revision, acquisition ownership, or outputs.
5. Generate editor constraints and presets from the catalog instead of duplicating undocumented limits in the browser. A preset may populate the editor but must never apply itself.
6. Separate edit, review, and apply. Any edit after review invalidates the review. The final review must show proposed values, units, effects, derived timing/rate values, and physical-output implications.
7. Keep Stop available during editing, review, and apply. Never let the review UI imply that output is electrically safe when only the custom logic has been observed at zero.
8. Revalidate the server independently. Browser validation is convenience, not the trust boundary.

## Contract shape

Each entry in `parameters` requires:

- `id`, `label`, `category`, `editable`, `data_type`, `units`;
- `safety_class`, `explanation`, and `change_effect`;
- for editable numeric parameters: `default`, `minimum`, `maximum`, and positive `step`;
- for fixed parameters: `value`;
- optional `tradeoff` for accuracy, latency, bandwidth, noise, or physical-output consequences.

Presets contain an `id`, `label`, `description`, and a non-empty `values` object referencing only editable parameters. Presets prepare a review; they do not mutate the board.

## Validate and review

```text
python scripts/critical_parameter_review.py validate <app-folder>
python scripts/critical_parameter_review.py review <app-folder>
```

Use `--json` for automation. Bind the reported contract SHA-256 into test or package evidence when parameter definitions affect the accepted application behavior.
