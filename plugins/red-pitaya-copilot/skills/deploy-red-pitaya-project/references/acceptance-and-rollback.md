# Acceptance and rollback

## Acceptance report

Record every test with:

- Test ID and purpose.
- UTC start/end.
- Exact board identity and deployment ID.
- Command/request performed.
- Expected result.
- Actual result and evidence.
- Pass/fail/blocked.
- Whether physical output was exercised.

## Safe-first acceptance sequence

Stop at the first critical failure.

### A. Invariants

- SSH fingerprint remains the verified value.
- Hostname, machine ID, serial/MAC, model, OS and role remain matched.
- `/opt/redpitaya` exact target and original ro/rw state are restored.
- No unplanned path changed.
- Stage and backup remain readable.
- Physical outputs remain disabled.

### B. Payload integrity

- Every planned live file exists with planned size/hash.
- No unexpected extra payload file was introduced.
- Effective Nginx/service/FPGA files are the intended copies.
- For overlay-profile loaders, the app-root bitstream/DTBO, permanent profile
  copies, and effective `fpga.conf` target all have the same qualified hashes.
- Backup manifest/hash verification still passes.

### C. Process and configuration health

- Nginx configuration test passes.
- Nginx workers remain healthy after graceful reload.
- Planned services have intended enabled/active state.
- No restart loop, fatal journal entry or new port collision exists.
- CPU/memory/disk behavior is plausible for the idle state.

### D. Read-only behavior

- App entry page loads.
- Record the direct app URL. If home-screen discoverability is required, verify
  that `/bazaar?apps=` contains the exact slug exactly once, the rendered
  launcher shows one tile, and that tile opens the intended route. Verify no
  backup, retired release or tile-less helper appears. A working direct route
  is not proof of launcher indexing.
- Status endpoint returns current board state with `cache: no-store` behavior.
- Unknown and invalid API actions are rejected.
- Version endpoint/build metadata identifies the deployed release.
- FPGA magic/build/register-map version matches when applicable.
- The persistent service recognizes the same identity through its real
  register backend. A direct `monitor` read alone is not service acceptance;
  record any bounded mmap-to-monitor fallback explicitly.
- Snapshot overlay/build identity, generator state, persistent counters,
  unrelated channels and service epoch; repeat status/version/health and
  read-only capture calls; require every non-owned state to remain unchanged.

For an authorized continuous generator test, keep the bounded signal visible
for at least ten seconds while performing those read-only calls. An output that
disappears after a few seconds is a lifecycle/reset failure until proven
otherwise, not automatically a burst-duration problem.

### E. Safe stop

Invoke Stop/Disable and verify independently:

- Service reports disabled.
- FPGA/output-override enable bit reads disabled where available.
- DAC output or external inhibit readback is safe.
- Repeated stop is idempotent.
- Stop works even if the browser is closed.

Failure here is critical and requires rollback or `FAILED_SAFE`.

### F. Authorized functional test

Only if user authorized physical behavior:

- Define amplitude/range/load limits first.
- Begin at minimum-risk settings.
- Use loopback or isolated test equipment.
- Verify intended channel and polarity.
- Verify stop during activity.
- Return to disabled state after test.

Do not energize an external system merely because the web UI renders correctly.

### G. Continuous acquisition

For event-monitoring/FPGA FIFO systems, test:

- FPGA authoritative total is monotonic.
- Descriptor sequence is monotonic within an epoch.
- Epoch changes are intentional and visible.
- FIFO level and high-water are plausible.
- Dropped/overflow count is exposed and remains zero in nominal testing.
- Software drain totals reconcile with FPGA totals, accounting explicitly for FIFO queued and dropped descriptors.
- Service reconnect/restart does not reset lifetime hardware totals silently.
- Timestamp progression and sample-rate assumptions are correct.
- Coverage metric represents monitored time, not process uptime alone.

## Rollback triggers

Rollback automatically in the deployment procedure when:

- Live file hash differs from plan.
- Mount state cannot be restored.
- Nginx config test fails after replacement.
- Required service cannot reach stable state.
- FPGA signature/register map is wrong.
- Output safe stop fails.
- Critical status/API acceptance fails.
- Another process changes a live path during commit.

For a non-critical cosmetic failure, keep outputs disabled and ask before deciding between repair and rollback.

A missing launcher tile with a working, explicitly accepted direct route is a
discoverability failure, not proof that the service failed. Do not add an
unplanned `controllerhf.so` to repair it. Preserve the committed state and
prepare a new package and plan if a tile is required.

On the verified OS 2.00-35 launcher, also inspect app-only packages for
`fpga.conf`. A literal `none` is not a no-op marker: the launcher treats it as
a bitstream path, `/bazaar?start=<slug>` returns error `-5`, and the tile can be
suppressed. Do not hot-edit the committed app. Omit the file in a new immutable
package and roll forward only through a new plan and authorization.

## Rollback procedure

1. Assert and verify safe stop.
2. Stop new/dependent services.
3. Record current failed state before overwriting evidence.
4. Validate backup manifest and hashes.
5. If required, remount exact `/opt/redpitaya` using the same guarded procedure.
6. For each destination:
   - Restore prior content when `existed_before` is true.
   - Remove only the exact newly introduced path when `existed_before` is false.
7. Restore Nginx fragment, systemd unit/drop-ins and enabled/active state.
8. Restore prior FPGA artifact/overlay and load it only if the prior state is known and safe.
9. `sync` and restore original mount state.
10. Re-run safe-first acceptance against the restored version.
11. Mark `ROLLED_BACK` only after acceptance passes.

If backup verification fails, do not perform a blind partial rollback. Keep the system disabled and hand off `FAILED_SAFE`.

## Roll-forward

Use roll-forward only when:

- The failure cause is understood.
- Live state and backup remain known.
- A new plan with new hashes/deployment ID is generated.
- User explicitly authorizes it.

Never edit a validated plan in place after staging. Generate a new one.

## Custom-register bus-abort acceptance

Do not make the first custom-register read in the persistent service. After
loading:

1. verify the loader and FPGA manager report completion;
2. run an expendable, time-bounded helper that reads identity/status only;
3. require normal exit and exact magic, ABI, capabilities, and build ID;
4. on SIGBUS, AXI external abort, timeout, or mismatch, stop dependent
   services and restore the known-good stock overlay immediately;
5. restart the service only after the rollback or exact identity succeeds.

`fpga_manager=operating` is not register-bus acceptance. It proves that the
configuration manager accepted the image, not that every custom slave returns
ACK.

For service roll-forward, also verify:

- the declared loopback port is listening;
- Nginx returns the real version/status response, not 502;
- immediate restart succeeds after closing an active WebSocket, using a short
  bounded readiness poll for listener/API startup;
- no MMIO worker remains orphaned after bind or startup failure.
