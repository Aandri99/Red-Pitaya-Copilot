# Deterministic offline application packaging

## Contents

1. Package contract
2. Create a directory bundle
3. Interpret readiness
4. Validate and detect tampering
5. Optional transport ZIP
6. Deployment handoff
7. Failure and recovery rules

## Package contract

`package_red_pitaya_app.py` creates the application directory shape consumed by
`$deploy-red-pitaya-project`. The bundle:

- is a new directory whose basename exactly equals `application.slug`;
- contains the app files and `redpitaya-project.json` directly at its root;
- contains no symlink, Windows reparse point, special file, secret-like file,
  embedded private key, embedded credential assignment, cache, or editor junk;
- normalizes regular file modes to `0644` and executable scripts/ELF files to
  `0755`;
- carries `application-package-receipt.json`, a canonical, hash-bound inventory
  and validator-provenance receipt;
- carries `DEPLOYMENT_BLOCKED.txt` whenever any validator warning remains;
- never claims deployment authorization.

The receipt is self-attested offline evidence. A valid receipt proves that the
directory still matches the bytes and validator results recorded when it was
created. It does not prove target identity, native ABI compatibility, safe
physical wiring, or authorization to install or energize anything.

The tool omits only explicit cache/junk entries and records their source-relative
paths in `excluded_cache_paths`. It rejects secrets, links, reserved package
metadata, special files, unsafe names, individual files larger than 512 MiB,
and payloads larger than 2 GiB.

## Create a directory bundle

Run from this skill directory:

```text
python scripts/package_red_pitaya_app.py create \
  --app <validated-app-folder> \
  --output <new-artifact-parent>/<application-slug> \
  --os-release 3.00-57
```

For OS 2 use the exact manifest release, currently `2.00-35` or `2.07-48`.
Both share the FPGA artifact family, but the asserted release must still equal
the target-bound manifest. The output parent must already exist, and the final output path must
not exist. Never use a generic folder name such as `bundle`; the deployment
validator requires the bundle root name to equal the application slug.

The packager first builds a sanitized copy in a unique sibling staging
directory, runs both the application validator and shared project validator in
build and strict/deployment-ready modes, writes the receipt, revalidates the
exact final staging directory, and publishes it with a no-replace rename.
Transient Windows antivirus/indexer sharing violations are retried with bounded
backoff. Existing destinations are never replaced.

Use `--json` before the `create` subcommand for machine-readable output:

```text
python scripts/package_red_pitaya_app.py --json create \
  --app <validated-app-folder> \
  --output <new-artifact-parent>/<application-slug>
```

## Interpret readiness

Packaging can succeed with unresolved warnings because a truthful offline
artifact is useful for review. The result then has:

```text
deployment_ready: false
deployment_authorized: false
classification: deployment-blocked-directory-bundle
```

`DEPLOYMENT_BLOCKED.txt` is mandatory in that case. Resolve the receipt's
blockers against the exact target and create a new package. Do not delete the
marker, edit the receipt, or patch payload files in place.

A warning-free result can have `deployment_ready: true`, but still has
`deployment_authorized: false`. The deployment skill must independently prove
board identity, backup and rollback readiness, staging integrity, service and
port compatibility, and user authorization.

## Validate and detect tampering

Revalidate a directory at any time:

```text
python scripts/package_red_pitaya_app.py validate \
  --package <artifact-parent>/<application-slug>
```

This check:

- rejects unknown, missing, changed, linked, secret-like, cache, or special
  payload files;
- requires canonical receipt bytes and recomputes `receipt_sha256`;
- recomputes every payload SHA-256 and the canonical inventory SHA-256;
- binds manifest target/application identity;
- binds the exact bundled validator paths and hashes;
- reruns both validators and compares fresh results to the receipt;
- enforces the blocked marker/readiness relationship.

Require warning-free deployment readiness explicitly when preparing a
deployment handoff:

```text
python scripts/package_red_pitaya_app.py validate \
  --package <artifact-parent>/<application-slug> \
  --require-deployment-ready
```

A valid but warning-bearing package fails this command by design.

## Optional transport ZIP

Create a reproducible ZIP in the same operation:

```text
python scripts/package_red_pitaya_app.py create \
  --app <validated-app-folder> \
  --output <new-artifact-parent>/<application-slug> \
  --transport-zip <new-artifact-parent>/<application-slug>-transport.zip
```

The ZIP has fixed timestamps, canonical member order and modes, stored
(uncompressed) members to avoid compressor-version variance, a fixed archive
comment, and a top-level `TRANSPORT_ONLY.txt`. It contains the directory bundle
under `<application-slug>/`. It is byte-for-byte reproducible for identical
sanitized payloads, validator versions, and findings.

The ZIP is never the deployment source and never grants authorization. Extract
it, select the contained directory, and validate that directory. To verify an
archive against its directory:

```text
python scripts/package_red_pitaya_app.py validate \
  --package <artifact-parent>/<application-slug> \
  --transport-zip <artifact-parent>/<application-slug>-transport.zip
```

## Deployment handoff

Pass the directory bundle, not the ZIP, to
`$deploy-red-pitaya-project`. The deployment file map must contain exactly one
application mapping rooted at the exact final app destination:

```json
{
  "schema_version": 1,
  "files": [
    {
      "source": "artifacts/my_app",
      "destination": "/opt/redpitaya/www/apps/my_app",
      "component": "app"
    }
  ]
}
```

The `source` is relative to the deployment project root. The deployment
manifest must byte-for-byte equal the bundle's `redpitaya-project.json`. The
deployment planner reruns deployment-ready app validation and independently
hashes the complete bundle, including packaging evidence.

## Failure and recovery rules

- If validation fails, no final package directory is published.
- If the final directory or ZIP already exists, stop; choose a new versioned
  artifact parent instead of overwriting evidence.
- If ZIP preflight fails, the directory is not published.
- If source bytes change while copying, abort and remove only the guarded
  staging directory.
- If publication is interrupted, inspect only sibling paths beginning with
  `.<slug>.package-staging-`; never use broad recursive cleanup.
- If validator code changes, an older receipt intentionally fails provenance
  validation. Revalidate the source and create a new package with the new tool.
