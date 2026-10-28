# Probe and identity

## Contents

1. Evidence authority
2. SSH host-key verification
3. Read-only collection
4. Probe record format
5. Identity comparison
6. Stop conditions

## 1. Evidence authority

Use this priority order:

1. Direct observations from the live physical target.
2. Files and loader scripts from the exact installed OS image.
3. Official documentation for the matching release.
4. General Red Pitaya knowledge.

Do not let a familiar hostname overrule conflicting serial, MAC, machine ID, OS, model or FPGA evidence.

Separate these concepts:

- **Network locator:** current hostname/IP used to reach something.
- **SSH identity:** cryptographic host-key fingerprint.
- **Linux identity:** hostname and `/etc/machine-id`.
- **Hardware identity:** board model, serial, EEPROM/DNA information and MAC addresses.
- **Deployment role:** generator, acquisition, controller or standalone; this is an installation decision, not an intrinsic hardware fact.
- **Project target:** OS/model/role required by `redpitaya-project.json`.

Require agreement between all available layers before a write.

The deployment plan copies normalized probe fields only for review. The
hash-bound normalized probe report remains authoritative. Plan validation with
`--project-root` re-reads that exact file and requires semantic equality for
connection, username, SSH fingerprint, identity, OS, model, role, original
mount state, capture time, probe source and warnings. Editing copied plan fields
cannot retarget a valid receipt.

## 2. SSH host-key verification

### First connection

1. Obtain the expected host-key fingerprint through a channel independent of the network being tested. Examples: a console session on the physical board, a trusted inventory record, or a person physically identifying the board.
2. A host-key scan may collect a candidate key, but does not authenticate it:

   ```text
   ssh-keyscan -t ed25519,rsa -- <address> > <scoped-known-hosts-candidate>
   ssh-keygen -lf <scoped-known-hosts-candidate>
   ```

3. Compare the displayed SHA-256 fingerprint with the independently obtained value.
4. Only after an exact match, place the verified entry in a deployment-specific known-hosts file.
5. Connect with:

   ```text
   ssh -o StrictHostKeyChecking=yes \
       -o UserKnownHostsFile=<scoped-known-hosts> \
       -o IdentitiesOnly=yes \
       <user>@<address>
   ```

Use an SSH agent, key file with restricted permissions, or an interactive prompt. Never use `sshpass`, a password in a URI, an environment variable printed by diagnostics, or `-o StrictHostKeyChecking=no`.

### Changed key

Stop. Do not delete the known-hosts entry merely to make the warning disappear. A changed key can mean:

- OS re-image.
- Board replacement or swapped DHCP lease.
- Hostname reassignment.
- Lost SSH host keys.
- A routing/DNS error.
- A man-in-the-middle attack.

Re-identify the physical board and verify its new fingerprint independently. Record why the change is expected and preserve the old fingerprint in the deployment record.

## 3. Read-only collection

Run individual commands so a failed command does not hide later evidence. Capture stdout, stderr, exit code and UTC collection time. Commands can differ by release; record `not available` instead of inventing a value.

### Network and Linux identity

```text
hostname
cat /etc/hostname
cat /etc/machine-id
ip -brief link
ip -brief address
cat /sys/class/net/eth0/address
```

If Wi-Fi is used, record its MAC separately. Do not assume `eth0` exists; list `/sys/class/net/`.

### OS and runtime

```text
cat /etc/os-release
uname -a
getconf GNU_LIBC_VERSION
python3 --version
gcc --version
systemctl --version
```

Search narrowly for Red Pitaya release evidence:

```text
find /opt/redpitaya -maxdepth 3 -type f \
  \( -iname '*version*' -o -iname 'git_info.txt' -o -iname 'ecosystem*' \) -print
```

Do not translate Ubuntu version into Red Pitaya OS release. They are separate facts.

### Board model and hardware identity

Use the target’s available evidence and preserve the raw output:

```text
/opt/redpitaya/bin/profiles -f
/opt/redpitaya/bin/monitor -n
/opt/redpitaya/bin/monitor -i
/opt/redpitaya/bin/monitor -f
cat /proc/device-tree/model
tr '\000' '\n' < /proc/device-tree/compatible
find /sys/firmware/devicetree/base -maxdepth 2 \
  \( -iname '*serial*' -o -iname '*model*' -o -iname '*dna*' \) -type f -print
```

Read the live stock `overlay.sh` and copy the non-empty output of the same model
helper without case conversion or marketing-name translation. The verified OS
2.00-35 loader uses `monitor -f`; newer builds may use `profiles -f`. Use
`monitor -n`, `monitor -i`, the matching FPGA directory and physical inventory
as corroborating evidence. Do not normalize `STEMlab 125-14 Z10` and
`STEMlab 125-14 Z20` to the same target.

### Exact `/opt/redpitaya` mount

```text
findmnt -T /opt/redpitaya -o TARGET,SOURCE,FSTYPE,OPTIONS
findmnt -no TARGET /opt/redpitaya
findmnt -no FSTYPE /opt/redpitaya
findmnt -no OPTIONS /opt/redpitaya
df -h /opt/redpitaya /tmp /root
```

The normal app partition in the analyzed 2.07-48 and 3.00-57 images is VFAT and may be mounted read-only. The live probe is authoritative. A mount target other than exact `/opt/redpitaya` is a blocking difference until understood.

### FPGA loader and current artifact

Locate, do not execute, the installed loader:

```text
find /opt/redpitaya -maxdepth 4 -type f \
  \( -name 'overlay.sh' -o -name 'fpga.sh' -o -name 'fpga.conf' \) -print
```

Read the exact loader script and record:

- Expected primary artifact filename.
- Device-tree overlay filename/path.
- Model/profile selection logic.
- Whether it stops applications or services.
- Whether it uses FPGA Manager, configfs or another interface.
- Whether it accepts a directory or a file.
- Its safe failure behavior.

Do not run a loader merely to identify it.

If a custom design exposes a read-only signature, magic value, build ID, register-map version or epoch, record it. If it does not, state that the live FPGA image cannot be positively identified.

### Processes, ports and services

```text
systemctl --no-pager --type=service --state=running
systemctl show nginx -p LoadState -p ActiveState -p SubState -p FragmentPath
ss -lntup
ps -eo pid,ppid,user,stat,lstart,cmd
```

Query only the services relevant to the project. Record the actual unit names; do not assume `nginx.service`, `redpitaya.service` or an app-specific name is present.

### Existing live files

For each planned destination:

- Record absent/present.
- Record file type, owner, group, mode, size and mtime.
- Hash regular files with SHA-256.
- Reject symlinked live destinations unless the plan explicitly models and validates the link.
- Record the effective Nginx include and systemd unit paths separately from project source paths.

## 4. Probe record format

Save a local JSON object like this. Replace placeholders with observed values; never insert credentials.

```json
{
  "probe_schema_version": 1,
  "captured_at": "2026-07-29T12:00:00Z",
  "probe_source": "verified interactive SSH read-only session",
  "connection": {
    "host": "replace-with-observed-host",
    "address": "192.0.2.10",
    "username": "root",
    "host_key_verified": true,
    "ssh_host_key_sha256": "SHA256:REPLACE_WITH_VERIFIED_FINGERPRINT"
  },
  "identity": {
    "hostname": "replace-with-observed-hostname",
    "machine_id": "replace-with-observed-machine-id",
    "serial": "replace-with-observed-serial",
    "mac_addresses": ["02:00:00:00:00:01"],
    "roles": ["acquisition"]
  },
  "os": {
    "release": "3.00-57",
    "ubuntu": "24.04.4",
    "kernel": "replace-with-observed-kernel",
    "python": "3.12",
    "glibc": "2.39"
  },
  "board": {
    "model": "STEMlab 125-14 Z20",
    "model_code": "z20_125",
    "profiles_model_code": "z20_125",
    "monitor_model_name": "STEMlab 125-14 Z20",
    "monitor_model_id": "replace-with-monitor-i-output"
  },
  "storage": {
    "opt_redpitaya": {
      "target": "/opt/redpitaya",
      "source": "replace-with-observed-source",
      "fstype": "vfat",
      "options": ["ro", "relatime"]
    }
  },
  "fpga": {
    "loader_script": "/opt/redpitaya/sbin/overlay.sh",
    "expected_artifact": "fpga.bin",
    "monitor_fpga_version": "replace-with-monitor-f-output",
    "loaded_signature": "unknown"
  }
}
```

The analyzer deliberately requires a base64 SSH SHA-256 fingerprint that
decodes to exactly 32 bytes, syntactically valid host/IP locators, immutable
identity fields, exact SSH username, a real UTC
`captured_at` value and a non-empty `probe_source`. A syntactically valid report
is evidence organization, not proof that the evidence was collected honestly.

## 5. Identity comparison

Require:

- Probe `os.release` equals manifest `target.os_release`.
- Probe `profiles -f` output equals manifest `target.model`.
- `monitor -n` and `monitor -i` corroborate the physical board.
- `monitor -f` is recorded only as FPGA-version evidence.
- Expected role is present in `identity.roles`.
- SSH fingerprint equals the independently verified value.
- Machine ID, serial and/or MAC values agree with the deployment inventory.
- Loader artifact name agrees with the matching OS profile.

If a board was intentionally re-imaged, machine ID and host key may change while hardware serial/MAC remain stable. Treat this as a documented identity transition, not an automatic match.

## 6. Stop conditions

Stop before staging when:

- Host key is absent, unverified or changed.
- OS release or model is missing or mismatched.
- Role is unclear.
- `/opt/redpitaya` resolves to an unexpected mount.
- Loader artifact behavior is unclear for an FPGA deployment.
- Existing live path is a surprising symlink/device/socket.
- Free space is insufficient for both stage and backup.
- Output state cannot be established before an operation that may change live
  configuration or hardware. A known active output may coexist with isolated
  `/tmp` staging, but must be recorded and verified disabled after the
  precommit gate and before backup or commit.
- Another deployment or FPGA owner is active and coordination is absent.
