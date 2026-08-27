# EdCore Automation 9950x Integration

This folder holds the sanitized 9950x-side integration for the dedicated
`edcore-automation` production VM (VMID `324`, LAN `192.168.50.82`). The guest
stack itself is defined under `docker/edcore-automation/`.

## Verified backup pull

The guest backup producer publishes:

```text
/var/backups/edcore-automation/current -> YYYYMMDDTHHMMSSZ/
```

Each immutable run contains `MANIFEST.json`, `SHA256SUMS`, and the logical
exports for Mosquitto, Node-RED, InfluxDB, Telegraf, the replay harness, and the
single Python automation runtime. `verify-backup.py` fails closed unless:

- the manifest has schema `edsys.edcore-automation.backup.v1`, the exact guest,
  Compose-project, service, artifact, image, run-ID, and timestamp contract;
- the artifact inventory exactly equals every regular payload file;
- `SHA256SUMS` exactly covers every regular file except itself;
- every SHA-256 digest matches; and
- the tree contains no symlink or other special-file entry.

`pull-backup.sh` uses a dedicated 9950x service key and the guest account
`edsys-backup`. It never loads Jeremy's SSH configuration, general-purpose
identity, agent, or `known_hosts`, and it never grants the timer a shell or
general guest sudo path. The guest forced-command gate accepts only these exact
requests:

```text
edsys-backup-current
edsys-backup-export YYYYMMDDTHHMMSSZ
```

The second request is allowed only for the run still selected by `current`.
Before streaming it, the root-owned guest gate rejects links, special files,
non-root-controlled content, and a failed manifest/hash verification. The 9950x
extractor independently rejects traversal, links, special files, and duplicate
archive paths. The pull then verifies again before atomic publication, rejects
a changed payload that reuses an accepted run ID, and retains 35 days by
default. The accepted stage is:

```text
/srv/edsys-backup/staging/edcore-automation/<run-id>/
/srv/edsys-backup/staging/edcore-automation/current
```

The EdSys Restic include set already selects the parent
`/srv/edsys-backup/staging` tree. Confirm that exact include before enabling:

```bash
sudo grep -Fx /srv/edsys-backup/staging /etc/edsys-backup/includes.txt
```

The pull is deliberately independent of the global Restic service. A guest,
SSH, or LAN failure must not block unrelated 9950x, NetBox, Foothills, Codex,
or other protected data. Failed pulls never replace `current` or an accepted
immutable run, so Restic continues to include the last verified copy already
under the selected staging parent. Restic reporting and automation-backup
freshness must remain separate signals.

### Dedicated backup transport provisioning

Do not reuse `~jeremy/.ssh/id_ed25519`, the Netdata deployment identity, an SSH
agent, or a general guest account. Do not enroll this transport with
`ssh-keyscan` or first-use acceptance. The client pin must come from the guest's
ED25519 host public key obtained through the trusted Proxmox console or guest
agent and compared with the fingerprint shown on that same trusted console.

On 9950x, place that **public** host-key record in a temporary root-owned file.
Copy the reviewed client provisioner into a root-only staging directory before
running it; never execute the root provisioning step from the mutable Git
checkout:

```bash
cd /srv/edsys/edsys-infrastructure
sudo install -d -o root -g root -m 0700 /root/edsys-automation-provision
sudo install -o root -g root -m 0755 \
  scripts/automation/provision-backup-client.sh \
  /root/edsys-automation-provision/provision-backup-client.sh
# /root/edcore-automation-ssh_host_ed25519_key.pub must be the console-verified public key.
sudo /root/edsys-automation-provision/provision-backup-client.sh \
  /root/edcore-automation-ssh_host_ed25519_key.pub
sudo stat -c '%U:%G %a %n' \
  /etc/edsys-secrets/edcore-automation-backup \
  /etc/edsys-secrets/edcore-automation-backup/id_ed25519 \
  /etc/edsys-secrets/edcore-automation-backup/known_hosts
```

The expected modes are `0700` for the dedicated secret directory and `0600`
for the private key and single-record `known_hosts`. The pin uses the isolated
alias `edcore-automation-backup`. The helper creates a new key only when neither
half exists and refuses a partial or mismatched keypair. Never copy or print
the private key. Transfer only the public
`/etc/edsys-secrets/edcore-automation-backup/id_ed25519.pub` to a root-only
staging directory on the guest through a trusted administrative channel.

On `edcore-automation`, stage the following reviewed public source files under
a root-owned `0700` directory and compare their SHA-256 hashes with the 9950x
source before execution:

```text
provision-guest-backup-reader.sh
guest-backup-export.py
guest-backup-ssh.sh
verify-backup.py
id_ed25519.pub                 # the dedicated 9950x public key only
```

Then run the staged provisioner locally from the trusted guest console:

```bash
sudo chown -R root:root /root/edsys-automation-provision
sudo chmod 0700 /root/edsys-automation-provision
sudo chmod 0755 \
  /root/edsys-automation-provision/provision-guest-backup-reader.sh \
  /root/edsys-automation-provision/guest-backup-export.py \
  /root/edsys-automation-provision/guest-backup-ssh.sh \
  /root/edsys-automation-provision/verify-backup.py
sudo chmod 0600 /root/edsys-automation-provision/id_ed25519.pub
sudo /root/edsys-automation-provision/provision-guest-backup-reader.sh \
  /root/edsys-automation-provision/id_ed25519.pub
sudo stat -c '%U:%G %a %n' \
  /usr/local/libexec/edsys-edcore-automation-backup-{export,ssh,verify} \
  /var/lib/edsys-backup/.ssh/authorized_keys \
  /etc/sudoers.d/edsys-edcore-automation-backup \
  /etc/ssh/sshd_config.d/05-edsys-backup-reader.conf
sudo visudo -cf /etc/sudoers.d/edsys-edcore-automation-backup
sudo sshd -t
sudo sshd -T -C user=edsys-backup,host=edcore-automation,addr=192.168.50.50 \
  | grep -E '^(allowusers|authenticationmethods|passwordauthentication|kbdinteractiveauthentication|permittty|disableforwarding|forcecommand)'
```

The resulting account has a locked password, a root-owned non-writable home,
no supplementary groups, and one root-owned authorized key with `restrict`,
`no-user-rc`, and the forced launcher. The `.ssh` directory is
`root:edsys-backup` mode `0750` and `authorized_keys` is
`root:edsys-backup` mode `0640`: the SSH authentication process can read the
key while the forced-command account cannot replace either path. Its only sudo
authorization is:

```text
edsys-backup ALL=(root) NOPASSWD: /usr/local/libexec/edsys-edcore-automation-backup-export *
```

The provisioner atomically installs the root-owned
`05-edsys-backup-reader.conf`, validates the entire effective daemon config with
`sshd -t`, and refuses to reload unless the unique effective `AllowUsers` set is
exactly `jeremy` plus `edsys-backup`. Its per-user `Match` policy requires
public-key authentication, the fixed force command, no password or keyboard
interactive authentication, no PTY/user RC/forwarding/tunnel, and one session.
It then reloads `ssh.service`; any validation/reload failure restores the prior
drop-in before exiting. This admits only the dedicated reader and does not
broaden Jeremy's authentication policy.

At the 2026-08-22 review, the guest's separate `PermitRootLogin` value was
`without-password`. This reader provisioner intentionally does not modify that
unrelated baseline. The guest stack bootstrap now owns a target of
`PermitRootLogin no`; final live acceptance after that bootstrap is **to be
confirmed** rather than broadened by this backup-reader exception.

That root program treats `SSH_ORIGINAL_COMMAND` as one opaque argument, accepts
only the two literal commands above, sanitizes the verifier/tar environment,
and never invokes a shell. Interactive shell, PTY, SFTP, forwarding, arbitrary
commands, non-current run IDs, and extra command arguments must all be refused.
After a valid guest backup exists, prove `edsys-backup-current` succeeds through
the dedicated key and pinned host record, then prove `uname -a`, SFTP, and a
forwarding request fail before enabling the timer. Do not relax the forced
command if one of those negative tests fails.

### Install the 9950x pull

Install the reviewed units on `9950x` only after the dedicated client and guest
forced-command account pass the acceptance above:

```bash
cd /srv/edsys/edsys-infrastructure
sudo install -d -m 0750 -o root -g root \
  /srv/edsys-backup/staging/edcore-automation
sudo install -d -m 0755 -o root -g root \
  /usr/local/libexec/edsys-edcore-automation
sudo install -m 0755 -o root -g root scripts/automation/pull-backup.sh \
  /usr/local/libexec/edsys-edcore-automation/pull-backup.sh
sudo install -m 0755 -o root -g root scripts/automation/verify-backup.py \
  /usr/local/libexec/edsys-edcore-automation/verify-backup.py
sudo install -m 0755 -o root -g root scripts/automation/extract-backup.py \
  /usr/local/libexec/edsys-edcore-automation/extract-backup.py
sudo install -m 0755 -o root -g root \
  scripts/automation/validate-installed-pull.py \
  /usr/local/libexec/edsys-edcore-automation/validate-installed-pull.py
sudo install -m 0644 -o root -g root \
  scripts/automation/systemd/edsys-edcore-automation-backup-pull.service \
  /etc/systemd/system/
sudo install -m 0644 -o root -g root \
  scripts/automation/systemd/edsys-edcore-automation-backup-pull.timer \
  /etc/systemd/system/
sudo /usr/local/libexec/edsys-edcore-automation/validate-installed-pull.py
sudo systemd-analyze verify \
  /etc/systemd/system/edsys-edcore-automation-backup-pull.service \
  /etc/systemd/system/edsys-edcore-automation-backup-pull.timer
sudo systemctl daemon-reload
sudo systemctl enable --now edsys-edcore-automation-backup-pull.timer
sudo systemctl start edsys-edcore-automation-backup-pull.service
```

The `ExecStartPre` ownership/mode validator must pass before enabling the timer.
It rejects linked, misowned, or mutable path components, requires exact `0755`
program and `0644` unit modes, and covers `pull-backup.sh`, `verify-backup.py`,
`extract-backup.py`, `validate-installed-pull.py`, and both systemd units. The
timer executes only the root-owned, non-group/world-writable copies under
`/usr/local/libexec/edsys-edcore-automation/`; it never executes scripts from
the operator-owned Git checkout. Re-run the reviewed `install` commands after
an accepted source update, then verify ownership and mode before restart:

```bash
sudo stat -c '%U:%G %a %n' \
  /usr/local/libexec/edsys-edcore-automation/{pull-backup.sh,verify-backup.py,extract-backup.py,validate-installed-pull.py} \
  /etc/systemd/system/edsys-edcore-automation-backup-pull.{service,timer}
```

The timer runs independently at 01:55 America/New_York, before the
authoritative 02:15 Restic timer. There is intentionally no `Requires=`,
`Wants=`, `Before=`, or `After=` dependency between the pull and global Restic.

Run the reviewed `scripts/ops/bootstrap-healthchecks.sh` on 9950x. It
idempotently creates or reconciles the dedicated check named
`edsys-edcore-automation-backup-pull` to a 26-hour timeout and two-hour grace,
maps it to the service, and privately installs its environment file at:

```text
/etc/edsys-healthchecks/edsys-edcore-automation-backup-pull.env
```

The root-owned `0600` file contains the private ping URL but the bootstrap does
not print its UUID:

```bash
cd /srv/edsys/edsys-infrastructure
scripts/ops/bootstrap-healthchecks.sh
sudo stat -c '%U:%G %a %n' \
  /etc/edsys-healthchecks/edsys-edcore-automation-backup-pull.env
```

The pull sends `start`, success, and `fail` signals through curl standard input;
the private URL is never placed in curl's process arguments or printed. This check
is the automation-backup freshness signal. A failed or missed pull alerts
separately while global Restic still protects the last verified immutable copy
and all unrelated data.

Restore into an isolated internal-only stack first. Never restore these files
over the live broker, automation engine, or time-series database.

## Uptime Kuma monitors

`configure-uptime-kuma-monitors.py` reconciles four monitors beneath the
existing `Voice and Automation` group:

- ICMP reachability for VMID `324` at `192.168.50.82`;
- TCP reachability for the TLS-only MQTT listener on `8883`;
- Node-RED HTTPS on `1880`, accepting its expected unauthenticated 2xx/3xx
  login/editor shell but no privileged API response; and
- InfluxDB HTTPS `/health` on `8086`.

Uptime Kuma 1.x cannot perform a mutual-TLS MQTT protocol probe. The `8883`
monitor therefore proves only TCP reachability of the TLS-exclusive listener;
the guest stack acceptance script is authoritative for CA validation, client
identity, ACL, publish/acknowledgment, and plaintext-port denial.

The two HTTPS monitors require the automation CA certificate. Supply its public
certificate bundle at runtime; the helper stores that public CA in only the two
HTTPS monitor rows and always leaves `ignore_tls=0`. It rejects private-key
material and does not copy CA keys, client certificates, passwords, MQTT
identities, or notification credentials into source control or monitor rows.

Stop Uptime Kuma and take the offline path explicitly. The helper independently
creates a private SQLite-consistent pre-change backup and refuses a container
that Docker still reports as running:

```bash
docker stop uptime-kuma
sudo scripts/automation/configure-uptime-kuma-monitors.py \
  --container-stopped --state enabled \
  --tls-ca-file /etc/edsys-secrets/edcore-automation-ca.crt
docker start uptime-kuma
```

Use `--state disabled` for an explicit rollback, or the default `preserve` to
retain existing states (new monitors start disabled). After restart, verify all
four heartbeats and the existing notification-provider bindings in the Uptime
Kuma UI. Restore a private database backup only while Uptime Kuma is stopped.

## Tests

```bash
python3 -m py_compile scripts/automation/*.py
bash -n scripts/automation/pull-backup.sh
python3 -m pytest -q scripts/automation/tests
```
