# Nginx and systemd activation

## Nginx

### Inspect

Record:

- Nginx executable path and version.
- Nginx compile arguments and whether access, Basic-auth, proxy, rewrite, Lua
  and WebSocket-required modules are actually present.
- Main configuration path.
- Effective app include chain.
- Actual service/process owner.
- Existing app route prefix.
- Whether Lua support is compiled/loaded.
- Effective CORS, caching and MIME behavior.

Prefer the effective configuration (`nginx -T` where supported) over assumptions from one source file.

Do not remove an application authentication-review gate merely because the
operator authenticated to an unrelated Web Console port. Prove that the exact
port-80/81 route is protected. When the target Nginx lacks access/auth modules,
use a service-owned operator credential or another reviewed boundary, forward
only the required authentication header, and keep secrets out of the app
bundle, URLs, logs and persistent browser storage.

### Stage validation

Before commit:

- Parse JSON files.
- Check route/action allow-lists match implemented handlers.
- Reject duplicate location blocks introduced by the change.
- Reject directives whose modules are absent from the target build. For
  example, `limit_except { deny all; }` is not portable to a build without the
  HTTP access module; the service must still reject wrong methods.
- Verify static paths stay beneath the app root.
- Verify Lua/shell actions validate parameters and use fixed executable paths.
- Verify status/stop endpoints cannot be cached.
- Verify no credential is embedded.

### Live activation

1. Install app/config files from verified staging.
2. Run the target’s Nginx configuration test with the discovered executable/configuration.
3. If it fails, preserve the exact error, restore prior files and do not reload.
4. If it succeeds and effective config changed, perform a graceful reload through the discovered service mechanism.
5. Confirm old workers exit and new workers remain stable.
6. Request:
   - Static entry page.
   - Read-only status endpoint.
   - Safe-stop endpoint.
   - One invalid route/action that must be rejected.
7. Confirm expected status code, content type, cache headers and JSON schema.

An HTTP 200 is insufficient if the body is an Nginx error page or stale cached content.

## systemd

### Unit design checks

Require:

- Absolute `ExecStart`/`ExecStop` paths.
- Dedicated user where hardware permissions allow it.
- Explicit working directory.
- No secrets in the unit or command line.
- Environment file with restricted permissions if configuration must be external.
- Restart policy that avoids tight failure loops.
- Startup ordering only for real dependencies.
- Safe stop in `ExecStop` or service signal handling.
- Defined behavior when FPGA signature/register map is wrong.
- Persistent state path outside `/tmp` if state must survive reboot.

Do not use `Type=forking` unless the program truly forks and supplies the required lifecycle semantics.

### Validation and activation

1. Validate staged unit syntax using tools available on the target release.
2. Back up existing unit and drop-ins.
3. Install executable/config before unit.
4. Install unit from verified staging.
5. Run daemon reload only after unit files are in place.
6. If a unit should be enabled, record its prior enabled state and change only that unit.
7. Start/restart only after dependencies, Nginx routes and FPGA contract are ready.
8. Inspect `systemctl show` state, exit status and journal.
9. Observe long enough to detect immediate restart loops.

### Rollback

Stop the new service, restore prior executable/config/unit/drop-ins, daemon-reload, restore prior enabled state and start only if it was previously active. Preserve safe stop while doing so.

## Dependency ordering

For an app that uses a service and FPGA:

```text
files -> unit/config -> Nginx config test/reload
      -> FPGA safe load/signature check
      -> service start
      -> status endpoint
      -> safe-stop test
      -> authorized functional test
```

If the FPGA is not part of this deployment, verify the expected existing signature before starting the service.
