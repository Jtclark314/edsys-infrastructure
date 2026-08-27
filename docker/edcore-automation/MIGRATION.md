# EdCore Automation Migration

Status: production migration and acceptance are **to be confirmed**. This is a
controlled cutover, not authorization to remove a working HA add-on because
the replacement container merely started.

## Invariants

1. HAOS keeps state/device authority, final actuation, Matter Server, ESPHome,
   Music Assistant, simple and safety-related automations, and HA backups.
2. Mosquitto on `edcore-automation` becomes the only broker only after every
   required client passes the migration gates below.
3. HA Mosquitto remains the rollback broker until **both Home Assistant and
   Frigate** pass functional and restart testing against the new mTLS broker.
4. HA Node-RED remains installed until the central Git Project, encryption,
   dependency/error paths, benign-flow test, and rollback test pass.
5. Commands are never retained. Replays can publish only under
   `edsys/test/v1/replay/<run-id>/...` and never to an actuator/control topic.
6. No production service is installed on `pve-edcore` or placed in
   `edcore-ops`.
7. Capture backups and exact rollback values before each client cutover; do
   not put those private values in Git or operator transcripts.
8. No project script or service runs as root from transferred source until the
   canonical tree is normalized and the fail-closed source guard passes.
9. Production command policy remains exactly `"allowed": []` until one
   target/action and its exact scalar parameter schema pass review and a benign
   end-to-end test. Parameters can never redirect HA service/entity/device/area
   or MQTT-topic authority.
10. The CA signing key and external Home Assistant/Frigate private keys leave
    the guest only after an independently cold-tested age escrow and explicit
    custody markers. `edsys-edge-livingroom` is unused and is not migrated.
11. Broker and data networks remain Docker-internal. Mosquitto and InfluxDB use
    a separate publication-only ingress bridge with inter-container traffic
    disabled and no new outbound traffic; only Node-RED has the egress network.
    Every service retains explicit CPU, memory, and PID limits.

## 0. Preflight and recovery capture

- [ ] Before any provisioning/rebuild, reconfirm VMID `324`, fixed
  `192.168.50.82`, DNS, DHCP, NetBox, and
  neighbor tables are unused immediately before provisioning.
- [ ] Reconfirm `pve-edcore` quorum/health, physical thin-pool free space, and
  recent VM backups. A 120 GiB logical disk requires ongoing physical-growth
  alerting even when the thin pool currently has space.
- [ ] Preserve the verified 7–14-day CPU/RAM evidence for `edcore-ops` and
  `edcore-sdr`; use an immediate representative SDR decoder/load test instead
  of waiting for another observation window.
- [ ] Back up HAOS, existing Mosquitto configuration/data, HA Node-RED
  configuration/flows/credentials, Frigate configuration, and relevant
  DNS/DHCP/NetBox records. Prove each artifact is readable.
- [ ] Inventory every MQTT client, client ID, source host, broker, TLS support,
  subscriptions, publishes, QoS, retained behavior, birth/will, discovery
  prefix, reconnect behavior, and owner. Unknown entries remain `to be
  confirmed`; do not grant a wildcard ACL to make them work.
- [ ] Record a sanitized topic sample. Exclude payloads containing credentials,
  audio/transcripts, personal names, exact presence history, or arbitrary
  serial bytes.
- [ ] Define a maintenance window, success owner, stop conditions, and exact
  rollback path. Ordinary HA control remains available throughout.
- [ ] Record the reviewed release identity and transfer method. The production
  guest receives an installed copy, not a developer-writable checkout; later
  updates repeat transfer, normalization, guard, and acceptance in that order.

## 1. Resource and guest transition

1. Gracefully stop `edcore-ops`, reduce it from 16 GiB to 8 GiB, start it, and
   verify QEMU agent, SSH, desktop/Codex administrative workflow, Netdata, and
   memory headroom. Restore 16 GiB on any unexplained pressure or workload
   regression.
2. Create VMID `324` as `edcore-automation`: 6 vCPU, 8 GiB initial RAM, 120 GiB
   disk, QEMU agent, `onboot=1`, startup order 50, and the approved fixed
   network identity. Use a minimal guest; do not clone desktop credentials.
3. Patch the guest, enable time sync, Docker/Compose, key-only SSH, firewall,
   Netdata child, backup user/path, and disk/thin-pool alerts. Reboot and prove
   cold-start recovery before application deployment. Prove SSH has
   `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
   `PermitRootLogin no`, and exactly `AllowUsers jeremy edsys-backup`; a
   bootstrap rerun must preserve the restricted backup-pull account.
4. Run the representative SDR decoder/load gate. If CPU/RAM/decoder health
   passes, gracefully reduce `edcore-sdr` from 12 GiB to 8 GiB and verify its
   receive-only profiles. Then grow `edcore-automation` to the 12 GiB target.
   Roll back either memory change on regression. Do not infer decoder health
   from idle RAM alone.

Do not change HAOS (8 vCPU/8 GiB) or NetBox (4 vCPU/8 GiB) as part of this
migration. NetBox Caddy repair is a separate fault-domain task.

## 2. Deploy an isolated control plane

- [ ] Transfer the reviewed stack as data to
  `/srv/edsys/edsys-infrastructure/docker/edcore-automation`. Create/review
  `.env` and run unit/Compose render checks as the unprivileged transfer
  account. End the transfer before any root-executed project command.
- [ ] Reject the transfer if it contains any symlink, special file, or source
  file with more than one hard link. Confirm every ancestor is `root:root` and
  not group/world-writable; normalize every stack directory to
  `root:root 0755`, `scripts/*.sh` and `node-red/entrypoint.sh` to
  `root:root 0755`, other source to `root:root 0644`, and `.env` to
  `root:root 0640`.
- [ ] Run `sudo scripts/source-guard.sh --transfer` as the first root-executed
  project command. It must pass before bootstrap, secret generation, deploy,
  verify, direct root Compose, backup, or restore. Re-transfer and normalize
  against the reviewed release on any failure; never make the guard permissive.
- [ ] On the normalized, guarded Ubuntu guest, run
  `sudo nft -c -f tests/fixtures/edsys-automation-firewall-ubuntu.nft`. This
  maintained fixture is an exact deterministic render of the tracked template
  except for its unique fixture table and safe `ens18` LAN interface. `nft -c`
  is check-only: it parses but does not apply the fixture or alter active rules.
  The exact command passed on the maintained Ubuntu 24.04 parser and the unique
  fixture table remained absent afterward.
- [ ] Run `sudo scripts/bootstrap-guest.sh --apply` and prove it installed
  `/usr/local/sbin/edsys-automation-source-guard` as `root:root 0755`.
  Confirm it rendered the tracked
  `firewall/edsys-automation-firewall.nft.in` only through
  `scripts/install-firewall.sh --apply`, atomically installed/applied the
  candidate, and then enabled the tracked firewall systemd unit. The installer
  must run its source guard before render, validate the exact interface and
  five-placeholder contract, stage the canonical candidate, and invoke the
  installed atomic helper without a separate `nft -c` preflight. On apply
  failure it restores only the prior canonical file, does not reapply it, and
  requires the normalized active-ruleset fingerprint (excluding counters) to
  remain unchanged. There is no inline bootstrap firewall or unit source.
  Confirm Compose, backup, and restore services use an exact `--runtime`
  `ExecStartPre`; the firewall service alone uses `--coherent` so it can start
  in either complete pre-deploy or runtime state. Every service command and
  working directory must match the guarded path inventory.
- [ ] Provision root-owned secrets and unique certificates as described in
  [README.md](README.md). Run `sudo scripts/generate-secrets.sh` with no
  arguments only on the exact `edcore-automation` host/path. Verify its
  no-link/no-hardlink gates, certificate CN-to-ACL identity, SANs, expiry, and
  scoped ownership/modes without printing secret contents.
- [ ] Render both base Compose and
  `compose.yaml` + `compose.bootstrap.yaml`, and run all repository tests before
  start. Confirm the base InfluxDB definition mounts only its TLS secrets.
- [ ] Use `sudo scripts/deploy.sh` for first deployment. Do not use raw
  `docker compose up`: before any Compose command or ingress attachment, the
  helper reruns `scripts/install-firewall.sh --apply` to atomically refresh the
  reviewed firewall and fails closed with the prior canonical file restored—but
  not reapplied—and the normalized active fingerprint unchanged if the
  replacement cannot apply. It then starts the steady Mosquitto/InfluxDB model,
  proves the exact effective non-null `NetworkSettings.Ports` mappings before
  reading the typed Influx `/api/v2/setup` state, uses the one-time override
  only when setup is allowed, and repeats the effective InfluxDB mapping gate
  after every bootstrap or steady recreate. It creates write-only Telegraf and
  read-only Grafana tokens without printing them. Require exactly one setup
  JSON document whose `allowed` value is boolean; the parser must return literal
  `true`/`false` strings successfully. An initialized `false` skips bootstrap
  but continues through token and dependent-service setup; missing, mistyped,
  malformed, or multiple JSON values stop deployment. It then force-recreates
  steady InfluxDB and fails if Docker inspect fails, finds a bootstrap mount,
  or finds any of the seven exact one-time overlay names:
  `DOCKER_INFLUXDB_INIT_MODE`, `DOCKER_INFLUXDB_INIT_USERNAME`,
  `DOCKER_INFLUXDB_INIT_PASSWORD_FILE`,
  `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE`, `DOCKER_INFLUXDB_INIT_ORG`,
  `DOCKER_INFLUXDB_INIT_BUCKET`, or `DOCKER_INFLUXDB_INIT_RETENTION`. The
  name-only gate intentionally permits the benign image-baked
  `DOCKER_INFLUXDB_INIT_CLI_CONFIG_NAME`; a broad
  `DOCKER_INFLUXDB_INIT_*` rejection is incorrect. Its sanitized pinned
  `linux/amd64` baseline is
  `tests/fixtures/influxdb-2.8.0-amd64-config-env-names.json`. Do not substitute a
  requested `HostConfig.PortBindings` entry or positive `ss` output for the
  effective mapping proof.
  It changes only `mosquitto/mosquitto.conf`, `mosquitto/aclfile`, and
  `mosquitto/aclfile-internal` to `1883:0 0640`, then requires a complete
  `--runtime` source-guard pass before Docker consumes the tree.
  If the named ingress network already exists, deploy must also fail closed
  before Compose unless it is a local, non-config-only bridge with exact
  non-internal/non-attachable/non-swarm-ingress state, disabled IPv6, complete
  options, default IPAM driver with null/empty IPAM options, the sole
  subnet/gateway, and only the expected Mosquitto and InfluxDB endpoints.
  No delete-or-reuse cleanup is attempted; do not delete, recreate, disconnect,
  or otherwise alter a mismatched network.
  Docker 29 may add `"IPRange": ""` to the sole IPAM Config item. Deploy and
  runtime verification accept exactly the item without `IPRange` or with only
  that empty field. A null/nonempty `IPRange`, any other extra IPAM key, or a
  second Config item must fail. Keep the sanitized executable regression at
  `tests/fixtures/docker29-ingress-network-inspect.json`; never replace it with
  raw live inspect state.
- [ ] Prove the rendered/running four-network contract: broker and data are
  internal; Mosquitto has broker+ingress; InfluxDB has data+ingress;
  runtime/harness have broker only; Telegraf has broker+data; and only Node-RED
  has broker+data+egress. A service with a published port must never be attached
  only to internal networks. Confirm every service has nonzero CPU, memory, and
  PID bounds.
- [ ] Prove the non-internal publication plane is exactly
  `edsys-edcore-automation-ingress`, bridge `br-ed-ingress`, subnet
  `172.31.82.16/29`, gateway `172.31.82.17`, Mosquitto `172.31.82.18`, and
  InfluxDB `172.31.82.19`, with
  `enable_ipv6: false` and `com.docker.network.bridge.enable_icc=false`.
  Confirm `br-ed-ingress` is exactly 13 ASCII bytes and `br-edsys-egress` is
  exactly 15 ASCII bytes; both must retain their safe characters and remain at
  or below Linux's 15-byte interface-name limit.
  Require runtime `EnableIPv6 == false` and established published
  return traffic before the firewall's unconditional drop for every new packet
  arriving from that bridge. Prove Mosquitto and InfluxDB cannot communicate
  laterally over ingress and cannot reach DNS, Home Assistant, or the Internet
  from ingress. Require Docker `"userland-proxy": false`, no
  `allow-direct-routing`, an input drop for `br-ed-ingress` before broad
  ICMP acceptance, then protocol-neutral
  `oifname "br-ed-ingress" counter drop` and direct `172.31.82.16/29`
  destination drops after the exact published DNAT allowances. From controlled
  LAN clients, prove InfluxDB
  succeeds from `192.168.50.50` but fails from a non-`192.168.50.50` source;
  direct container-IP and unpublished-port reachability must also fail.
- [ ] Prove the egress plane is exactly bridge `br-edsys-egress`, subnet
  `172.31.82.0/28`, gateway `172.31.82.1`, and Node-RED `172.31.82.2`.
  Forwarding may allow only established traffic, DNS to `192.168.50.5`/`.6`
  on TCP/UDP 53, and Home Assistant `192.168.50.75:8123`; require a bounded
  logged drop for every other packet from that bridge. Prove HA connects and
  `1.1.1.1:443` does not. Any future integration destination/port needs an
  explicit Compose+nft+positive/negative-probe review; never add general
  egress.
- [ ] Prove Telegraf uses MQTT keepalive `60s`, fixed numeric `json_v2` value
  ingestion with no producer-controlled tags, and bounded `basicstats`
  aggregation. Confirm selected analytics do not replace or load HA Recorder.
- [ ] Prove Node-RED's active Git Project is release `1.0.2`, its credential
  artifact decrypts only with the root-owned secret, and both literal and
  localized `*.status.connected` broker states produce the exact connected
  health file. A missing/wrong secret or wrong release fails deployment.
- [ ] If optional backup/restore Healthchecks are enabled, install only
  `/etc/edsys-secrets/edcore-automation/healthchecks/backup.env` and
  `/etc/edsys-secrets/edcore-automation/healthchecks/restore-test.env` as
  regular `root:root 0600` files with one private
  `HC_PING_URL=https://...` assignment. Confirm the scripts pass the URL to
  `curl --config -` through stdin rather than argv.

### Required secret-escrow and custody gate

Complete every item after deploy creates the scoped tokens and before running
the production verifier:

- [ ] On `9950x`, generate and retain the native-age identity only at
  `/etc/edsys-secrets/edcore-automation-escrow/identity.txt`, `root:root 0600`.
  Install only its single public recipient line on the guest at
  `/etc/edsys-escrow/edcore-automation-recipient.txt`, `root:root 0644`. Prove
  the identity file is absent from the guest.
- [ ] Before escrow or unused disposition, run
  `sudo scripts/verify-edge-ingestion.sh --accept`. Prove the temporary,
  never-delivered edge identity publishes only the exact synthetic source
  `edsys/v1/telemetry/environment/edge-livingroom/synthetic`; a quiet-window
  trace contains exactly its hashed/pseudonymized event. Its topic suffix and
  payload `source` must be distinct, exact, independent deterministic SHA-256
  pseudonyms of their respective inputs and contain no raw edge identity;
  replay publishes only
  under `edsys/test/v1/replay/<run-id>/...`; `command-audit` observes no HA
  command; and Influx `selected_telemetry` has count 1 with min=max=mean at the
  unique synthetic value. Require
  `/etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json`,
  `root:root 0600`, schema
  `edsys.edcore-automation.synthetic-ingestion-acceptance.v1`.
- [ ] On the guest, run `sudo scripts/create-secret-escrow.sh --create`. Record
  the emitted SHA-256 and securely transfer the root-only
  `edcore-automation-secrets-<UTC>.tar.age` ciphertext to an absolute,
  root-controlled path on `9950x`; never transfer plaintext secrets back.
- [ ] Validate tracked `scripts/verify-secret-escrow.sh` and
  `scripts/secret_escrow_archive.py` unprivileged on `9950x`, then install the
  exact reviewed verifier at
  `/usr/local/sbin/edsys-automation-verify-secret-escrow`, `root:root 0755`,
  and the no-shebang helper at
  `/usr/local/libexec/edsys-automation-secret-escrow-archive.py`,
  `root:root 0644`, single-link, beneath a root-owned/non-group/world-writable
  path chain. Require fixed `PATH=/usr/sbin:/usr/bin:/sbin:/bin`, unset
  `PYTHONHOME`/`PYTHONPATH`, and isolated bytecode-disabled `python3 -I -B` for
  both the helper and inline acceptance generator.
  Invoke the installed path as root with exactly one absolute `.tar.age`
  argument. Require one bounded root-private regular decrypted temp, followed
  by a complete metadata inspection before any extraction. Reject absolute or
  traversal paths, duplicate normalized paths, links, devices/FIFOs/sockets or
  other special/PAX members, unsafe modes, member-count overflow, and excessive
  per-file/aggregate size. Require exact caps of 32 MiB per plaintext archive,
  512 members, 4 MiB per regular file, and 16 MiB aggregate regular-file
  payload; allow only contained directories and regular files beneath the
  expected root. Capture the verifier's single stdout acceptance
  JSON as a root-only `0600` file; any decrypt/inspect/extract, required-file,
  certificate, or CA-pair failure stops migration. Require every restored
  server/client private key to match its certificate, not merely a valid CA
  signature.
- [ ] Transfer only the acceptance JSON back and install it exactly as
  `/etc/edsys-escrow/edcore-automation-accepted.json`, `root:root 0600`. Prove
  schema `edsys.edcore-automation.secret-escrow-acceptance.v1`, archive basename,
  and SHA-256 agree with the guest's current ciphertext.
- [ ] Securely deliver the dedicated credentials only to Home Assistant and
  Frigate. After each custodian accepts its matching pair, run
  `sudo scripts/record-client-delivery.sh homeassistant --accepted` and
  `sudo scripts/record-client-delivery.sh frigate --accepted`. These markers
  prove custody only; they do not satisfy the later functional cutover gate.
- [ ] Do **not** deliver an edge key. Run
  `sudo scripts/record-client-disposition.sh edsys-edge-livingroom --unused`
  and prove its exact `unused-not-delivered` disposition contains the SHA-256
  of the matching ingestion acceptance. Do not create an edge delivery marker.
- [ ] Run `sudo scripts/finalize-online-keys.sh --apply`. Prove it removed the
  CA signing key/serial and Home Assistant, Frigate, and unused edge private
  keys only after validating the ingestion acceptance, disposition hash,
  certificate, escrow, and HA/Frigate delivery chain. Retain required public
  material and broker-resident identities.
  Require root-only
  `/etc/edsys-escrow/online-keys-finalized.json` with schema
  `edsys.edcore-automation.online-key-finalization.v1` matching the accepted
  archive.
- [ ] Prove the normal application backup carries the accepted `.tar.age`
  under its original basename plus `secret-escrow/ACCEPTANCE.json`, and never
  carries/decrypts the plaintext secret tree. Require the ingestion acceptance,
  edge disposition, HA/Frigate delivery markers, and finalization JSON beneath
  `custody-evidence/`. Prove restore uses the read-only `command-audit`
  identity—not Home Assistant—to test for retained commands.

- [ ] Run `sudo scripts/verify.sh`. It must validate the online-key
  finalization, exact listener/firewall, invalid-candidate firewall preservation,
  `edsys-edcore-automation-broker` helper network, four-plane/resource
  isolation, steady Influx state, mTLS/ACL, retained discovery, live
  retained-request rejection, post-restart empty-command namespace, exact
  Node-RED release/status/encryption, Telegraf, and event-harness checks before
  its backup/restore timers are allowed to start.
- [ ] Require every `mosquitto_pub` and `mosquitto_sub` acceptance probe to use
  an explicit, nonzero client ID containing only lowercase ASCII letters,
  digits, and hyphens and bounded to 23 bytes. Concurrent probes must use
  distinct IDs; a zero-length/default client ID is forbidden. Accept a negative
  TLS, auth, or timeout test only when its exit result and broker diagnostic
  prove the intended result and explicitly exclude a zero-ID protocol rejection.
  For a command-write ACL probe, the pinned MQTT v5 publisher may return zero on
  reason 135, so publisher exit status is not denial proof. Require one exact
  debug `PUBACK (Mid: 1, RC:135)`, one exact
  `Warning: Publish 1 failed: Not authorized.`, an accepted publisher session,
  and a concurrent authenticated zero-delivery audit on the exact topic that
  ends only in a clean timeout.
  Require an exact missing-client-certificate reason after a nanosecond log
  boundary rather than unrelated generic TLS text. A timeout also requires the
  exact client's accepted-session broker entry and no subscribe,
  authentication, protocol, transport, DNS, or client-ID error.
- [ ] Prove 8883 mTLS, retained state/discovery, ACL grants/denials, birth/will,
  and broker restart behavior.
- [ ] From a LAN peer, prove 8884 is closed. With an authorized internal test
  identity, publish a retained, short-lived request; prove the runtime rejects
  the **live** delivery through MQTT v5 Retain As Published and publishes no
  command. Clear the retained request and verify the production
  request/command namespaces are empty before continuing.
- [ ] Keep `automation-runtime` policy exactly `"allowed": []`. Before any
  future rule, prove its item has exact `{target, action, parameters}` and exact
  `{required, properties}`; permit only boolean, bounded finite numeric, or
  bounded enum-string schemas. Prove missing/extra/nested, `NaN`/infinity,
  range/enum, and authority-redirect (`entity_id`, `device_id`, `service`,
  `target`, `topic`, and peers) cases fail closed.
- [ ] Prove secured Node-RED HTTPS/admin authentication, Git-backed active
  Project, manual workflow, disabled palette installation, and encrypted
  `flows_cred.json`. A missing/wrong secret must prevent startup rather than
  create new credentials.
- [ ] Confirm Node-RED can write only
  `edsys/v1/automation/request/nodered`; confirm it cannot publish
  `edsys/v1/command/ha/#`.
- [ ] Add InfluxDB/Telegraf only after broker stability. Use selected telemetry
  and bounded retention; prove RF/high-rate aggregation and that HA Recorder is
  unchanged. No active general Grafana on `9950x` was found in the latest live
  preflight. Retain the read-only scoped token only as readiness for a future,
  explicitly approved external query integration; its consumer is **to be
  confirmed** and is not a migration blocker.
- [ ] Produce, pull to `9950x`, verify, encrypt off-host, and restore the first
  application backup in isolation.

After finalization, future client issuance is an offline operation: the client
creates and retains its own key, sends only a reviewed CSR to the recovery
custodian, and receives only its signed certificate and public CA chain. The
accepted escrow may be decrypted only in an isolated root-only work area on
`9950x`; neither the CA key nor a client key returns to the broker guest. The
maintained CSR/offline-signing ceremony remains **to be confirmed**, so a
future identity is not authorization to restore online issuance ad hoc.

Stop here and roll back the stack if any listener becomes public/wildcard,
Node-RED starts without authentication/encryption, an ACL is broader than the
inventory, a retained command can be accepted/replayed, or the installed
source guard reports an unsafe owner, mode, ancestor, link, file type, systemd
path, or mixed transfer/runtime state.

## 3. Migrate telemetry before control

For each non-actuating producer, one at a time:

1. issue a unique client certificate and narrow ACL;
2. add QoS 1 birth/will/availability with bounded freshness expectations;
3. point it at mTLS 8883;
4. prove only documented topics can be written/read and denied topics stay
   denied;
5. verify consumer parity, reconnect, broker restart, and loss/staleness
   behavior; and
6. retain the old setting and rollback instructions until the full migration
   closes.

Migrate selected environmental/energy telemetry before RF/high-rate streams.
Retained messages are limited to justified state/discovery; availability and
all requests/commands remain non-retained.
`edsys-edge-livingroom` is explicitly dispositioned unused for this release;
do not deliver/configure device credentials or include it in this migration
checklist. Its temporary synthetic-acceptance key was escrowed and removed by
the custody gate.

## 4. Migrate Home Assistant and Frigate

Home Assistant and Frigate are a single broker-retirement gate even when their
configuration changes occur separately.

### Home Assistant

- [ ] Confirm a current full HA backup and record the old broker setting.
- [ ] Install/use only the already accepted dedicated `homeassistant` client
  CA/certificate/key from the custody gate; point the MQTT integration to 8883
  with server verification enabled. Do not recover another copy from the guest.
- [ ] Verify integration setup, discovery/state restoration, subscribe/publish
  ACLs, birth/will, entity availability, restart, and broker restart.
- [ ] With an explicitly reviewed benign policy rule, send one unique,
  short-lived command through the internal request gate. Require immediate
  validator ack and separate final HA outcome ack. Remove or disable the test
  rule afterward.
- [ ] Prove expired/duplicate/unauthorized and retained command tests do not
  actuate anything.

### Frigate

- [ ] Record and back up its existing MQTT configuration.
- [ ] Install/use only the already accepted dedicated Frigate client identity;
  point Frigate to mTLS 8883 with server verification. Do not recover another
  private-key copy from the guest.
- [ ] Verify connection, expected topics/events, HA Frigate devices/entities,
  availability, event/update behavior, Frigate restart, broker restart, and
  the 9950x/Frigate recovery path.

### Joint soak without elapsed waiting

Do not wait passively or watch dashboards for days. Immediately execute the
representative test matrix: reconnect loops, broker restart, HA restart,
Frigate restart, 9950x support-service restart where safe, network interruption,
and stale/duplicate/retained negative cases. Use timestamped monitoring and
logs as evidence, then perform a deliberate rollback to the old broker and
forward cutover once. Any unexplained entity loss, event loss, reconnect
storm, duplicate action, or TLS bypass fails the gate.

**Do not retire HA Mosquitto until every HA and Frigate checkbox plus the
rollback/forward test passes.**

## 5. Migrate Node-RED

1. Inventory every HA Node-RED flow, node/module version, HA server reference,
   credential, context store, endpoint, timeout, and side effect. An empty
   inventory must still be evidenced; do not assume it.
2. Rebuild/import into the central Git Project without copying plaintext
   credentials. Replace direct command outputs with short-lived
   `automation/request/nodered` envelopes and outcome-ack handling.
3. Add explicit dependency timeout, status, catch/error, degraded-mode, and
   recovery paths. Keep safety/deterministic rules in HA.
4. Record a sanitized fixture, run event-harness self-test, replay only into a
   unique test run namespace, and verify no production publication occurs.
5. Review and commit the Project diff, restart Node-RED, verify active release
   `1.0.2`, exact localized-connected health, Project credential decryption,
   and then run a benign end-to-end test with both acks.
6. Prove rollback to the HA instance and forward again. Confirm only one copy
   of every flow is enabled during each test.

**Do not retire the HA Node-RED add-on until all six steps pass.** Disable it
first, verify HA restart and automation behavior, preserve the rollback backup,
and uninstall only in the approved cleanup window.

## 6. Retire overlap and review UniFi

After the joint HA/Frigate gate and Node-RED gate pass:

1. disable the HA Mosquitto add-on without deleting data; verify all clients
   remain on `edcore-automation`, restart HA/Frigate/new broker, and rerun the
   broker-restart/no-stale-command test;
2. uninstall HA Mosquitto only after the disabled-state rollback test passes;
3. disable HA Node-RED, prove the central Project is sole flow authority, then
   uninstall only after its rollback test passes;
4. search all configs, DNS, monitors, docs, and dashboards for the former
   broker/flow endpoints and remove only confirmed-stale references; and
5. separately back up and review the HA UniFi add-on. Confirm
   `edrouter-node0` is the authoritative controller and that HA relies on the
   intended integration before disabling/removing the redundant add-on.

At no point run dual-active brokers or duplicate enabled flows. A retained
state/discovery authority is not permission to retain actuator requests.

## 7. Event replay and production acceptance

The event harness accepts sanitized JSONL and derives all replay destinations
under `edsys/test/v1/replay/<run-id>/...`. Use a new run ID for each test:

```bash
docker compose --env-file .env --profile tools run --rm event-harness self-test
docker compose --env-file .env --profile tools run --rm event-harness record --help
docker compose --env-file .env --profile tools run --rm event-harness replay --help
```

Review the exact CLI arguments shown by the pinned source before recording or
replaying. Reject any trace containing a request, command, actuator, control,
credential, audio/transcript, exact-presence, or arbitrary serial topic/data.
Never point a test consumer at a production output.

Final acceptance requires evidence for:

- [ ] Internet loss: HA, MQTT, Node-RED, and ordinary local control continue.
- [ ] `9950x` loss: HA, MQTT, Node-RED, and selected ingestion continue;
  future external visualization/heavy support may be unavailable without
  blocking control.
- [ ] Guest/broker restart: client availability recovers and no stale actuator
  command is emitted.
- [ ] HA/Frigate/Node-RED rollback and forward cutovers pass.
- [ ] Topic denials, mTLS identity isolation, internal-listener exposure, and
  Node-RED auth/encryption gates pass.
- [ ] AI/invalid input cannot bypass the exact scalar policy schema, redirect
  authority, or directly actuate; production returns to `"allowed": []` after
  any reviewed benign acceptance rule.
- [ ] Docker network membership exactly matches broker/data/ingress and
  Node-RED-only egress boundaries; all three effective published-port mappings
  are exact and non-null; non-`192.168.50.50` InfluxDB, direct container-IP,
  unpublished-port, ingress lateral, and ingress outbound denials pass; and
  every running service retains CPU/memory/PID limits.
- [ ] Steady InfluxDB has no bootstrap mount/environment, Telegraf cardinality
  is bounded, and HA Recorder remains authoritative for HA history.
- [ ] The accepted secret escrow, bounded synthetic edge-ingestion acceptance,
  unused edge disposition/hash, HA/Frigate delivery markers, and
  `online-keys-finalized.json` all agree; the normal backup carries the matching
  age ciphertext, acceptance JSON, and complete `custody-evidence/` chain.
- [ ] An invalid firewall candidate fails without changing the active ruleset,
  and Node-RED release `1.0.2` has exact localized-connected health.
- [ ] Application-consistent backup, verified 9950x pull, encrypted off-host
  copy, and isolated restore pass.
- [ ] Netdata/endpoint/backup monitoring includes `edcore-automation`, and
  final DNS/DHCP/NetBox/service docs agree.

Only then mark production status current and remove `to be confirmed` labels.

## Rollback triggers

Immediately stop the affected migration stage and restore its captured config
for any loss of ordinary HA control, unexplained Frigate entity/event loss,
TLS verification bypass, public/wildcard admin listener, overly broad ACL,
Node-RED credential decryption failure, Influx/Telegraf backpressure affecting
control, duplicate actuation, accepted/replayed retained command, or failed
installed-source guard.

Rollback one client/service at a time. Preserve logs containing metadata only,
record timestamps and result codes, and do not delete the failed replacement
state until the cause is understood. Keep the new broker isolated if command
retention or ACL separation is in doubt.
