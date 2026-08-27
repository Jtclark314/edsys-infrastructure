# EdCore Automation Fabric

Status: deployable source is being prepared; production deployment and
acceptance are **to be confirmed**.

This stack is the dedicated production automation tier for EdCore. Home
Assistant remains the device/entity authority and final actuation boundary.
Nothing in this directory belongs on the `pve-edcore` Proxmox host itself or
in the rebuildable `edcore-ops` desktop VM.

## Guest target

| Setting | Target |
| --- | --- |
| Name | `edcore-automation` |
| Proxmox node / VMID | `pve-edcore` / `324` |
| Network | fixed `192.168.50.82/24`; reverify before any rebuild |
| CPU | 6 vCPU |
| Memory | 8 GiB initially; 12 GiB target after the resource gate |
| Disk | 120 GiB thin-provisioned; monitor physical pool growth |
| Startup | Proxmox startup order 50 |
| Guest | minimal supported Ubuntu LTS, Docker Engine, Compose v2, QEMU agent |

The guest must use ordinary host firewalling, monitoring, time sync, key-only
operator SSH, unattended security maintenance, and off-guest backup. It must
not receive a desktop, browser, Codex credentials, Docker socket proxy, or
general-purpose application workload.

Bootstrap enforces `PasswordAuthentication no`,
`KbdInteractiveAuthentication no`, and `PermitRootLogin no`, while preserving
the exact allowlist `AllowUsers jeremy edsys-backup`. The second account is the
restricted pull-backup path; rerunning bootstrap must not lock it out.

The initial guest firewall permits LAN clients only to MQTT 8883 and permits
the canonical `9950x` operator/monitor host to SSH 22, Node-RED 1880, and
InfluxDB 8086. Port 8884 is never host-published. Add another management source
only through an explicit reviewed firewall change.

Keep the existing 1 Gbps EdCore link. It is ample for voice, sensors, MQTT,
and this automation plane. Reassess 2.5 GbE only from measured video, backup,
or bulk-telemetry saturation; link speed is not a migration gate.

## Authority and traffic boundaries

```text
HAOS / Frigate / trusted edge clients
          |  mTLS, TCP 8883 (exact LAN bind)
          v
  authoritative Mosquitto
          ^                    ^
          | internal mTLS 8884 | internal mTLS 8884
       Node-RED          command validator
          | request only       | validated command only
          +--------------------+-----------------> HA final actuation

selected telemetry -> Telegraf -> InfluxDB -> future approved query client
sanitized JSONL ----> event harness -> edsys/test/v1/replay only
```

Mosquitto has two deliberately different listener contracts:

- **8883/external:** exact-LAN-published mutual TLS. Per-client certificate
  identities and ACLs permit only the required telemetry, state, discovery,
  availability, acknowledgment, and final HA command subscriptions. Retained
  state/discovery remains available for compatible clients. No external
  identity may write an automation request or final command.
- **8884/internal:** never published by Compose and still requires mutual TLS.
  Node-RED may submit only its request topic; `automation-runtime` alone may
  publish validated HA commands. Mosquitto retention capability is global and
  remains enabled for legitimate 8883 discovery/state. Instead, the runtime
  subscribes with MQTT v5 Retain As Published, rejects retained requests, and
  hard-codes `retain=false` on commands and acks; the exclusive ACL makes this
  the only request/command write path.

Do not try to move `retain_available` into a listener block: Mosquitto defines
it as a [global option](https://www.mosquitto.org/man/mosquitto-conf-5.html#retain_available).
The ACL, trusted publisher, Retain As Published rejection, expiry, and restart
tests above are the reviewed non-retention controls for the command namespace.

Every command includes a canonical UUIDv4, UTC creation and expiry, bounded
TTL, allowlisted target/action, optional correlation ID, and acknowledgment.
The SQLite ID ledger blocks duplicate delivery. Accepted means validation and
broker publication succeeded; a separate Home Assistant outcome ack is still
required. No flow may treat an accepted ack as proof of physical actuation.
If a QoS 1 publication wait times out, delivery is unknown rather than failed:
the runtime preserves the claimed command ID and returns
`publish_outcome_unknown`. Do not release/reuse that ID or automatically issue
a replacement command; reconcile broker/HA outcome evidence first.

See [the topic contract](mosquitto/TOPICS.md) and the reviewed, initially empty
[command policy](runtime/config/policy.json).

### Command authorization boundary

Production starts and remains fail-closed with `"allowed": []` until a
specific benign workflow is reviewed. Each future allowlist item has exactly
`target`, `action`, and `parameters`; `parameters` has exactly `required` and
`properties`. A property may be only a boolean, a finite integer/number with
an exact inclusive minimum and maximum, or a bounded string whose value is in
an explicit nonempty enum. The policy does not permit arbitrary strings,
objects, arrays, nulls, or producer-defined schemas.

At request time every required property must be present, no unreviewed
property may be present, and every value must match its reviewed scalar type,
range, and enum. `NaN`, positive/negative infinity, missing/extra values,
nested values, and range/enum violations are rejected before publication.
The authority-redirect keys `area_id`, `device_id`, `domain`, `entity_id`,
`service`, `service_data`, `target`, `targets`, and `topic` are forbidden even
if someone attempts to add them to a policy. A reviewed target/action selects
the fixed Home Assistant command destination; request parameters can never
redirect service, entity, device, area, MQTT topic, or final actuation.

## Services

| Service | Purpose | Permanent UI / listener |
| --- | --- | --- |
| Mosquitto | One authoritative event broker | mTLS 8883 on the exact LAN address; internal 8884 is unexposed |
| Node-RED | Advanced fusion and cross-system orchestration | authenticated HTTPS 1880 on the exact LAN address; initially firewall-limited to `9950x` |
| `automation-runtime` | Fail-closed command validation and duplicate-ID ledger | none |
| InfluxDB | Selected environmental, energy, RF, and high-rate analytics | HTTPS 8086 on the exact LAN address; firewall-limited to `9950x` |
| Telegraf | Selected MQTT ingestion and high-rate aggregation | none |
| Event harness | Sanitized record/replay test tool | one-shot CLI only; test namespace only |

InfluxDB does not replace Home Assistant Recorder. This stack does not add
Grafana, n8n, AppDaemon, or another rule engine. Add code automation to
the single small Python runtime only after a concrete workflow is shown to be
inappropriate for both HA and Node-RED.

### Network and resource isolation

Compose has four explicit planes. `edsys-edcore-automation-broker` and
`edsys-edcore-automation-data` remain Docker-internal networks. Mosquitto uses
broker plus the publication-only ingress plane; `automation-runtime` and the
opt-in event harness use only broker. InfluxDB uses data plus ingress; Telegraf
bridges only broker and data.

`edsys-edcore-automation-ingress` is the non-internal publication plane needed
for Docker to instantiate the exact host bindings. It is fixed to bridge
`br-ed-ingress`, subnet `172.31.82.16/29`, gateway `172.31.82.17`,
Mosquitto `172.31.82.18`, and InfluxDB `172.31.82.19`, with
`com.docker.network.bridge.enable_icc=false`. Inter-container communication on
that bridge is disabled. `br-ed-ingress` is exactly 13 ASCII bytes and the
existing `br-edsys-egress` is exactly 15 ASCII bytes; both use only safe
interface-name characters and fit Linux's 15-byte name limit. Do not lengthen
either bridge name. Guest forwarding accepts established published-port
return traffic, then drops every new packet arriving from the ingress bridge;
Mosquitto and InfluxDB therefore gain neither lateral access nor outbound
egress from this attachment. The host daemon must retain
`"userland-proxy": false` and must not set `allow-direct-routing`. The firewall
drops new input from `br-ed-ingress` after established traffic but before
broad ICMP acceptance. After the exact published DNAT allowances, a
protocol-neutral `oifname "br-ed-ingress" counter drop` and direct
`172.31.82.16/29` destination drop deny every other route. This denies
direct container-IP and unpublished-port reachability rather than treating
either as alternate ingress.
The reviewed source is
`firewall/edsys-automation-firewall.nft.in`; both bootstrap and deploy invoke
`scripts/install-firewall.sh --apply` to render the exact LAN interface and
atomically install/apply it. The installer validates the interface and exact
five-placeholder template contract, stages the rendered candidate as the
canonical file, and calls the installed atomic apply helper; it does not run a
separate `nft -c` preflight. If apply fails, it restores only the prior
canonical file without reapplying it and requires the normalized active-ruleset
fingerprint (excluding changing counter values) to remain unchanged. Deploy
refreshes this firewall before its first Compose command, so no container can
attach to the non-internal ingress bridge under stale rules. The ingress network
also has exact `enable_ipv6: false`, which runtime verification confirms as
`EnableIPv6 == false`.

If the named ingress network already exists, deploy must inspect it before any
Compose mutation and fail closed unless it is a local, non-config-only bridge
with non-internal/non-attachable/non-swarm-ingress state, disabled IPv6, the
complete option set, default IPAM driver with null/empty IPAM options, the sole
subnet/gateway, and only the expected Mosquitto and InfluxDB endpoints. A
mismatch or unknown endpoint is an investigation
stop. No delete-or-reuse cleanup is attempted: deploy does not delete,
recreate, disconnect, or otherwise alter the network to make the check pass.
Docker 29 may serialize the sole IPAM item with an additional `"IPRange": ""`.
Deploy and runtime verification accept exactly either the two-key
`Subnet`/`Gateway` item or that same item with only the empty `IPRange` field.
A null/nonempty `IPRange`, any other extra IPAM key, or a second Config item
fails closed. The maintained sanitized regression input is
`tests/fixtures/docker29-ingress-network-inspect.json`; it contains no live
identifier or runtime state.

Node-RED remains the sole service attached to the separate
`edsys-edcore-automation-egress` network, in addition to broker and data.
That plane is fixed to bridge `br-edsys-egress`, subnet `172.31.82.0/28`,
gateway `172.31.82.1`, and Node-RED address `172.31.82.2`. Guest forwarding
allows established traffic, Node-RED DNS to `192.168.50.5` and
`192.168.50.6` on TCP/UDP 53, and Node-RED API traffic to Home Assistant at
`192.168.50.75:8123`; all other traffic arriving from the bridge is logged
at a bounded rate and dropped. Verification requires the HA path to connect
and a direct `1.1.1.1:443` probe to fail. Published host listeners and the
guest firewall remain separate controls; network attachment is not
authorization to a topic or API. Any future Node-RED integration egress needs
an explicit review of the destination/port, Compose address plan, atomic nft
rules, negative probes, and rollback—never a general outbound allow rule.

Every service has explicit CPU, memory/reservation, and PID limits. Telegraf
uses 60-second MQTT keepalives and `json_v2` to accept only the fixed numeric
`value` field; producer-controlled tags are not stored. Its bounded
`basicstats` aggregator reduces selected RF/high-rate measurements before
InfluxDB. Revisit the limits from observed load, but do not remove them or add
producer-selected cardinality to hide backpressure.

## Repository layout

```text
compose.yaml                 pinned, hardened production services
compose.bootstrap.yaml       one-time InfluxDB initialization overlay
.env.example                 non-secret/site-specific values only
firewall/                    reviewed nftables template rendered before Compose
mosquitto/                   listener, ACL, and topic contracts
node-red/                    secured runtime and Git Project seed
runtime/                     command gate, event harness, and backup helpers
scripts/                     fail-closed VM and guest preparation helpers
telegraf/                    selected telemetry and aggregation policy
tests/                       standard-library contracts and live-parse fixture
MIGRATION.md                 controlled HA/Frigate/Node-RED migration
```

Runtime state, `.env`, certificates, private keys, hashes, tokens, JSONL
traces, databases, logs, and backups are excluded from Git.

## Installed-source trust boundary

Never run a production root operation from a developer-owned checkout or from
a directory that the transfer account can still modify. Transfer the reviewed
release **as data** to the exact guest path
`/srv/edsys/edsys-infrastructure/docker/edcore-automation`, create and review
the untracked `.env`, run the read-only tests as the transfer account, end the
transfer, and only then normalize ownership and modes. Do not `git pull` into
the installed tree or make it group-writable for convenient updates.

The accepted transfer state is exact:

- every path component from `/` through the stack is `root:root` and is not
  group/world-writable; every directory inside the stack is `root:root 0755`;
- each `scripts/*.sh` file and `node-red/entrypoint.sh` is `root:root 0755`;
- every other tracked/source file is `root:root 0644`;
- the untracked runtime `.env` is `root:root 0640`; and
- the Mosquitto configuration and both ACLs are `root:root 0644` during
  transfer. Deployment changes **only** `mosquitto/mosquitto.conf`,
  `mosquitto/aclfile`, and `mosquitto/aclfile-internal` to `1883:0 0640` for
  the runtime state. No other source file receives a service UID.

Before normalization, stop if either read-only inspection below prints a
path. A source symlink, device, FIFO, socket, or file with more than one hard
link is not acceptable; replace the transfer from reviewed source rather than
trying to bless it with ownership changes.

```bash
STACK=/srv/edsys/edsys-infrastructure/docker/edcore-automation
find "$STACK" -xdev \! -type d \! -type f -print
find "$STACK" -xdev -type f -links +1 -print
```

With the transfer session ended, normalize the reviewed copy using root shell
commands, not a project script. Preserve a stricter ancestor mode if one is
already required; otherwise `0755` is the baseline. Do not apply these
commands to a development checkout.

```bash
STACK=/srv/edsys/edsys-infrastructure/docker/edcore-automation
for path in /srv /srv/edsys /srv/edsys/edsys-infrastructure \
  /srv/edsys/edsys-infrastructure/docker; do
  sudo chown root:root "$path"
  sudo chmod go-w "$path"
done
sudo chown -R root:root "$STACK"
sudo find "$STACK" -xdev -type d -exec chmod 0755 {} +
sudo find "$STACK" -xdev -type f -exec chmod 0644 {} +
sudo find "$STACK/scripts" -xdev -type f -name '*.sh' -exec chmod 0755 {} +
sudo chmod 0755 "$STACK/node-red/entrypoint.sh"
sudo chown root:root "$STACK/.env"
sudo chmod 0640 "$STACK/.env"
```

Only after that sequence may the first root-executed project command run:

```bash
sudo "$STACK/scripts/source-guard.sh" --transfer
```

The guard independently walks the entire source tree, rejects symlinks,
special files, hardlinks, unsafe ancestors, owners, and modes, and fails
closed. Bootstrap/deploy install it as the root-owned executable
`/usr/local/sbin/edsys-automation-source-guard`. Compose, backup, and restore
services call it with `--runtime` in `ExecStartPre`; the early firewall uses
`--coherent` so it accepts either complete transfer or complete runtime state,
never a mixed state. `--coherent` is not a deployment shortcut. A guard
failure means stop, compare with the reviewed release, re-transfer if needed,
and normalize again; never loosen the check.

## Secret preparation

Provision every file referenced under Compose `secrets:` beneath the fixed
root-owned guest directory `/etc/edsys-secrets/edcore-automation/`. Run
`scripts/generate-secrets.sh` only as root, with no arguments, on the host whose
short hostname is exactly `edcore-automation`; it accepts no alternate output
path. It runs the installed coherent source guard and rejects symlinks, special
files, and hard-linked regular files rather than following or repairing them.

Secret directories are `root:root 0750`. The temporary CA signing key and the
one-time Home Assistant/Frigate plus synthetic-edge keys are `0400`;
broker-resident server/client keys and container-readable random secrets are `0440`; public
certificates are `0444`; and the CA serial, when present, is `0600`. Scoped
InfluxDB token files begin empty at `0440` and are populated only through the
deployment API. Do not relax these modes to make a container start. Required
secret classes are:

1. a temporary automation CA and distinct broker/server/client certificates
   and keys for every MQTT identity, including the read-only `command-audit`
   identity used for restore verification;
2. Node-RED HTTPS key/certificate, bcrypt admin-password hash, credential
   encryption secret, and session secret where configured;
3. InfluxDB HTTPS key/certificate, bootstrap password/token, and separate
   least-privilege Telegraf write and future/external query-read tokens (the
   latter retains the implementation filename `grafana_token`).

Do not share client private keys. Certificate Common Names are MQTT usernames,
so each CN must exactly match its ACL stanza. Include the external automation
DNS name and internal service DNS names in the appropriate server certificate
SANs. Containers receive only the CA certificate and their own identity.

After the installed-source transfer guard passes, the generator creates the
fixed layout without displaying credential values and refuses an incomplete
CA/certificate pair. It does not finish scoped InfluxDB tokens; those remain a
deploy-time gate.

### Offline secret escrow and external-client custody

The CA and external-client private keys are permitted on the guest only for
initial generation, encrypted escrow, and one-time delivery. Use this exact
order after `sudo scripts/deploy.sh` creates the scoped InfluxDB tokens:

1. On `9950x`, create a dedicated native `age` identity at
   `/etc/edsys-secrets/edcore-automation-escrow/identity.txt`, owned
   `root:root` and mode `0600`. Keep that identity and every decrypted work
   area off `edcore-automation`. Transfer only its single public `age1...`
   recipient line to
   `/etc/edsys-escrow/edcore-automation-recipient.txt` on the guest as
   `root:root 0644`.
2. Before escrow or disposition, run
   `sudo scripts/verify-edge-ingestion.sh --accept` on the guest. This is a
   bounded synthetic acceptance of the issued-but-never-delivered
   `edsys-edge-livingroom` identity, not an edge-device migration. It publishes
   once to the exact source topic
   `edsys/v1/telemetry/environment/edge-livingroom/synthetic`, records a quiet
   window, and requires exactly one pseudonymized environmental event. The
   topic suffix and payload `source` are independent deterministic SHA-256
   pseudonyms of their respective inputs; they must be distinct, exact, and
   contain no raw edge identity. It then
   replays that hashed event only beneath
   `edsys/test/v1/replay/<run-id>/...` while `command-audit` proves no HA
   command, and requires one InfluxDB `selected_telemetry` aggregate whose
   count is 1 and min/max/mean all equal the unique synthetic value. Success
   atomically creates root-only
   `/etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json`
   with schema
   `edsys.edcore-automation.synthetic-ingestion-acceptance.v1`. The edge ACL
   has no request/command access; this test does not authorize delivery.
3. On the guest, run `sudo scripts/create-secret-escrow.sh --create`. It
   creates an atomic root-only native-age archive named
   `edcore-automation-secrets-<UTC>.tar.age` beneath
   `/var/backups/edcore-automation-secret-escrow/` and prints only its SHA-256.
   Copy the ciphertext, never the identity, to a root-controlled absolute path
   on `9950x`.
4. Validate the tracked `scripts/verify-secret-escrow.sh` and
   `scripts/secret_escrow_archive.py` as an unprivileged user on `9950x`, then
   install those exact reviewed files as
   `/usr/local/sbin/edsys-automation-verify-secret-escrow`, `root:root 0755`,
   and `/usr/local/libexec/edsys-automation-secret-escrow-archive.py`,
   a `root:root 0644` regular file with link count 1. Every installed-path
   ancestor must be root-owned and not group/world-writable; the Python helper
   intentionally has no shebang and is invoked only by the installed verifier.
   That verifier fixes `PATH=/usr/sbin:/usr/bin:/sbin:/bin`, unsets
   `PYTHONHOME` and `PYTHONPATH`, and runs both the installed helper and inline
   acceptance generator with isolated, bytecode-disabled `python3 -I -B`.
   Invoke the installed verifier as root with exactly one absolute `.tar.age`
   path. Treat the decrypted tar as hostile: the verifier decrypts exactly once
   into a bounded root-private regular temporary file, inspects every member's
   full metadata before extraction, and permits only contained directories and
   regular files below the single expected root. Absolute/traversal paths,
   duplicate normalized paths, symlinks, hardlinks, devices, FIFOs, sockets,
   special/PAX surprises, unsafe modes, too many members, and excessive
   individual or aggregate size all fail closed. Exact caps are a 32 MiB
   plaintext archive, 512 members, 4 MiB per regular file, and 16 MiB total
   regular-file payload. Only after that complete pass may it extract into the
   root-only `/dev/shm` work area. It then checks
   required recovery material, verifies every server/client private key matches
   its certificate as well as the CA key pair, and writes one acceptance JSON
   object to stdout without printing a secret.
5. Capture that stdout as a `root:root 0600` file on `9950x`, transfer it back
   through the protected operator path, and install it on the guest exactly as
   `/etc/edsys-escrow/edcore-automation-accepted.json`, `root:root 0600`.
   Its archive name and SHA-256 must match the guest's current ciphertext.
6. Deliver each dedicated private key exactly once to its final custodian.
   The marker helper independently rejects a mismatched certificate/key pair.
   Only after Home Assistant accepts its matching pair, run
   `sudo scripts/record-client-delivery.sh homeassistant --accepted`; do the
   same for Frigate with
   `sudo scripts/record-client-delivery.sh frigate --accepted`. These markers
   attest delivery of the matching certificate hash, not successful broker
   migration; the functional migration gates still apply.
7. `edsys-edge-livingroom` is **not** part of this migration and receives no
   private key. Record that exact decision with
   `sudo scripts/record-client-disposition.sh edsys-edge-livingroom --unused`.
   The command requires the ingestion acceptance above, validates its schema
   and certificate hash, and embeds the acceptance file's SHA-256 in the
   root-only disposition. Do not create an edge delivery marker.
8. Run `sudo scripts/finalize-online-keys.sh --apply`. It verifies the cold
   escrow acceptance, both client-delivery markers, the synthetic-ingestion
   acceptance, and the edge disposition/hash/certificate chain; removes the
   online CA key/serial plus Home Assistant, Frigate, and unused edge private
   keys; and atomically writes the root-only evidence marker
   `/etc/edsys-escrow/online-keys-finalized.json` with schema
   `edsys.edcore-automation.online-key-finalization.v1`.

Do not run `scripts/verify.sh` until this finalization succeeds. The normal
application backup carries the accepted `.tar.age` ciphertext under its
original filename together with `ACCEPTANCE.json`. It also requires and copies
the ingestion acceptance, edge disposition, HA/Frigate delivery markers, and
online-key finalization JSON under `custody-evidence/`; it never decrypts or
copies the plaintext secret tree. Restore testing uses the broker-resident,
read-only `command-audit` identity to prove
`edsys/v1/command/ha/#` has no retained command. It never restores or mounts a
Home Assistant private key.

For a future client, create its private key on the client, send only a reviewed
CSR to the offline recovery custodian, decrypt the accepted escrow in an
isolated root-only work area on `9950x`, sign against the matching ACL/CN
inventory, and return only the signed certificate and public CA chain. The
client key and CA key must not return to the broker VM. A maintained offline
signing helper and ceremony remain **to be confirmed** before adding another
client; do not improvise by restoring the CA online.

### Optional Healthchecks heartbeats

Backup and restore-test heartbeats are optional. If enabled, create only the
fixed files
`/etc/edsys-secrets/edcore-automation/healthchecks/backup.env` and
`/etc/edsys-secrets/edcore-automation/healthchecks/restore-test.env` as regular,
non-symlink `root:root 0600` files. Each contains one private assignment of the
form `HC_PING_URL=https://...`; use `sudoedit` and never put the URL in Git,
shell history, logs, or this document. The scripts validate the fixed file and
HTTPS-only value, then pass the URL to `curl` through stdin configuration—not
the process argument list. A heartbeat transport failure is nonfatal to the
backup/restore result; the underlying job result remains authoritative.

Create `.env` from `.env.example`, review every value, and set its final
ownership/mode during the transfer normalization above. A documented address
is not rebuild authority: reconfirm VMID, address, DNS, DHCP, NetBox, and
neighbor availability immediately before recreating the guest. The reviewed
`scripts/provision-vm.sh` is dry-run by default and refuses a used identity,
non-quorate cluster, image checksum mismatch, or insufficient physical free
space; `--apply` is an explicit, destructive boundary.

## Validate and deploy

Run the first two checks as the unprivileged transfer account before source
normalization. Then complete the installed-source procedure above. The guard
must pass before **any other** `sudo` project script; bootstrap, secret
creation, deployment, and acceptance are explicit root mutations and do not
alter HA clients.

```bash
python3 -m unittest discover -s tests -v
docker compose --env-file .env config --quiet
docker compose --env-file .env -f compose.yaml -f compose.bootstrap.yaml config --quiet
sudo scripts/source-guard.sh --transfer
sudo nft -c -f tests/fixtures/edsys-automation-firewall-ubuntu.nft
sudo scripts/bootstrap-guest.sh --apply
sudo scripts/generate-secrets.sh
sudo scripts/deploy.sh
sudo scripts/verify-edge-ingestion.sh --accept
# Complete the ordered escrow, HA/Frigate delivery, unused edge disposition,
# and online-key finalization procedure above before production acceptance.
sudo scripts/verify.sh
sudo /usr/local/sbin/edsys-automation-source-guard --runtime
sudo docker compose --env-file .env ps
```

The maintained fixture is an exact deterministic render of the tracked
firewall template except for its unique
`edsys_automation_nft_parse_fixture` table and safe `ens18` LAN interface.
Run the shown root command only on the normalized, guarded Ubuntu guest. The
`nft -c` invocation is **check-only**: it parses the fixture but does not apply
it or change the active ruleset. This exact command passed on the maintained
Ubuntu 24.04 parser, and the unique fixture table remained absent afterward.

`scripts/deploy.sh` is the only supported first-deployment path. It refuses the
wrong host/address, missing/expiring identity, or missing non-secret `.env`;
refreshes and atomically applies the reviewed firewall before any Compose
command or ingress attachment; renders/tests the base and one-time bootstrap
Compose models; and starts the steady Mosquitto/InfluxDB definitions first.
Before querying the typed
`/api/v2/setup` state and applying `compose.bootstrap.yaml` only when setup is
still allowed, it requires Docker's effective `NetworkSettings.Ports` to hold
the exact non-null `192.168.50.82:8883` and `192.168.50.82:8086` mappings.
The setup parser slurps exactly one JSON document, requires an object with a
boolean `allowed` value, and converts that boolean to the literal raw string
`true` or `false` before jq exit-status evaluation. Thus `false` is a successful
initialized-state result: it skips the bootstrap override and continues to
scoped-token and dependent-service setup. Missing, mistyped, malformed, or
multiple JSON values stop deployment.
It repeats the InfluxDB binding gate after each bootstrap or steady
force-recreate, creates a write-only Telegraf token and read-only Grafana token
without printing either, then force-recreates InfluxDB from base
`compose.yaml`. A name-only Docker-inspect gate proves the steady container has
neither bootstrap secret mounts nor any of the seven exact one-time overlay
names: `DOCKER_INFLUXDB_INIT_MODE`, `DOCKER_INFLUXDB_INIT_USERNAME`,
`DOCKER_INFLUXDB_INIT_PASSWORD_FILE`,
`DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE`, `DOCKER_INFLUXDB_INIT_ORG`,
`DOCKER_INFLUXDB_INIT_BUCKET`, or `DOCKER_INFLUXDB_INIT_RETENTION`. It
intentionally permits the benign image-baked
`DOCKER_INFLUXDB_INIT_CLI_CONFIG_NAME`; a broad
`DOCKER_INFLUXDB_INIT_*` rejection is incorrect. The sanitized name-only pinned
`linux/amd64` baseline is
`tests/fixtures/influxdb-2.8.0-amd64-config-env-names.json`. Inspect failure is
fatal, and this proof occurs before any dependent starts. Deployment then
starts the full bounded stack and verifies service
health, effective bindings, exact Node-RED Project release `1.0.2`, encrypted
Project credentials, localized MQTT `connected` status, Telegraf connections,
and the event-harness self-test. A requested `HostConfig.PortBindings` entry is
not proof that Docker instantiated the mapping, and positive `ss` listener
output is not required when `userland-proxy` is disabled. Do **not** replace
this workflow with a raw first `docker compose up`.

`scripts/verify.sh` is the production-acceptance boundary. It proves exact
effective Docker port mappings and firewall state, and deliberately submits an
invalid atomic firewall candidate to prove the active ruleset remains
unchanged. It also proves the exact four-network topology, ingress
ICC/outbound/lateral denials,
per-service resource limits, steady Influx bootstrap-secret removal, accepted
escrow/key finalization,
HTTPS health, mTLS and no-certificate denial, command ACL denial for every
non-runtime identity, retained HA discovery, live retained-request rejection
through Retain As Published, zero command output, zero retained commands after
a broker restart, exact Node-RED release/localized connected
status/encryption integrity, Telegraf ingestion, and the event-harness guard.
Its MQTT helper attaches to `edsys-edcore-automation-broker`. Only after all
checks pass does it enable the nightly backup and weekly restore-test timers.
Every `mosquitto_pub` and `mosquitto_sub` acceptance probe must carry an
explicit, nonzero client ID made only from lowercase ASCII letters, digits, and
hyphens and bounded to 23 bytes; a zero-length/default client ID is forbidden,
and concurrently active probes must have distinct IDs. A negative TLS or auth
test passes only when its exit result and broker diagnostic prove the intended
denial or timeout and explicitly exclude a zero-ID protocol rejection. For a
command-write ACL probe, the pinned MQTT v5 publisher may return zero on reason
135, so publisher exit status is not denial proof. Require one exact debug
`PUBACK (Mid: 1, RC:135)`, one exact
`Warning: Publish 1 failed: Not authorized.`, an accepted publisher session,
and a concurrent authenticated zero-delivery audit on the exact topic that ends
only in a clean timeout.
The no-certificate probe requires an exact missing-client-certificate reason
after its nanosecond log boundary, never unrelated generic TLS text. A timeout
also requires the exact client's accepted-session broker entry, with no
subscribe, authentication, protocol, transport, DNS, or client-ID error.

The root-owned installed source guard also runs before verify and in every
root systemd application service. A source-guard failure prevents Compose,
firewall, backup, or restore execution; it is an integrity stop condition, not
a request to repair permissions in place without comparing reviewed source.

Before production promotion, prove all of the following from separate client
identities:

- anonymous, no-certificate, wrong-CA, and wrong-topic connections fail;
- retained state/discovery on 8883 works where explicitly allowed;
- external request/command writes fail;
- InfluxDB 8086 succeeds from the authorized `9950x` address
  `192.168.50.50` and is denied from a controlled non-`192.168.50.50` LAN
  source; direct `172.31.82.18`/`172.31.82.19` container-IP and unpublished
  port probes are denied;
- 8884 is unreachable from the guest LAN; an authorized retained request is
  rejected by the runtime, produces no command, is cleared after the test, and
  leaves the production command namespace empty;
- Node-RED HTTPS authentication and admin API protection pass; its active
  project is Git-backed and its credential file decrypts only with the
  root-owned secret;
- an unauthorized, expired, future-dated, over-TTL, malformed, and duplicate
  command each receives a stable rejection;
- an allowed benign command is non-retained, receives the immediate validator
  ack and a distinct final HA outcome ack;
- Telegraf stores selected telemetry, aggregates RF/high-rate input, and does
  not alter HA Recorder;
- broker/container/guest restarts produce availability changes without
  replaying an actuator command; an MQTT v5 command expires at the envelope's
  bounded expiry even if a consumer is temporarily disconnected.

The automated `verify.sh` proves the stack-local subset. HA, Frigate, real
benign actuation, failure drills, and rollback/forward cutovers remain the
controlled migration gates below; a green stack test cannot retire an add-on.

Follow [MIGRATION.md](MIGRATION.md); do not retire either HA add-on early.

The harness runs only through its opt-in tools profile. First pass the runtime
source guard, then record into its named volume, validate the entire trace
before any connection, and dry-run before a test publication:

```bash
sudo /usr/local/sbin/edsys-automation-source-guard --runtime
sudo docker compose --env-file .env --profile tools run --rm event-harness self-test
sudo docker compose --env-file .env --profile tools run --rm event-harness \
  record --output /var/lib/automation-event-harness/<sanitized-trace>.jsonl \
  --duration 300 --max-events 10000
sudo docker compose --env-file .env --profile tools run --rm event-harness \
  replay --input /var/lib/automation-event-harness/<sanitized-trace>.jsonl \
  --run-id <unique-test-run> --dry-run
```

Remove `--dry-run` only after inspecting the sanitized JSONL and unique
derived namespace. Replay publishes QoS 1 with `retain=false`.

## Routine operations

```bash
# Fail closed before direct root Compose access
sudo /usr/local/sbin/edsys-automation-source-guard --runtime

# State and health
sudo docker compose --env-file .env ps
sudo docker compose --env-file .env logs --since 30m --no-color \
  mosquitto node-red automation-runtime influxdb telegraf

# Revalidate rendered configuration and repository contracts
sudo docker compose --env-file .env config --quiet
python3 -m unittest discover -s tests -v

# Narrow restart only after dependency/actuation impact review
sudo docker compose --env-file .env restart <service>
sudo docker compose --env-file .env ps <service>
```

Change Node-RED only through its Git-backed `edcore-automation` Project.
Review the diff, test with a sanitized replay, commit the Project, and then
promote it. Never edit credentials into flows or Git. Every dependency needs
an explicit timeout, status, catch/error, and recovery path.

Monitor guest CPU/RAM/disk, thin-pool physical allocation, container health,
MQTT/TLS probes, client availability age, rejection/duplicate rates, Telegraf
buffering, Influx cardinality/storage, backup age, and restore rehearsal age.
No active general Grafana instance on `9950x` was found in the latest live
preflight. The read-only `grafana_token` is readiness for a future, explicitly
approved external query integration; its current consumer is **to be
confirmed** and is not a deployment blocker. Do not deploy Grafana on the
automation VM merely to consume the token.

## Backup and recovery

Back up application-consistent copies of Mosquitto persistence, the entire
Node-RED `/data` Project/configuration, InfluxDB engine/metadata, the command
ledger, and only intentionally retained sanitized harness traces. Never back
up by blindly copying a live SQLite or Influx engine directory.

The guest produces a hashed backup set; `9950x` pulls it with
`scripts/automation/pull-backup.sh`, verifies the manifest/checksums, and only
then includes it in encrypted off-host backup. Perform an isolated restore at
least weekly during migration and on the established schedule afterward.

```bash
sudo /usr/local/sbin/edsys-automation-source-guard --runtime
sudo scripts/backup.sh
sudo scripts/restore-test.sh
systemctl status edsys-automation-backup.timer \
  edsys-automation-restore-test.timer --no-pager
```

The backup script refuses a stopped core service or dirty Node-RED Project,
forces a Mosquitto persistence checkpoint, creates a verified Git bundle,
uses the Influx backup API, transactionally copies and integrity-checks the
SQLite ledger, includes root-only sanitized trace evidence, carries the
accepted age ciphertext with its original archive basename plus the matching
`secret-escrow/ACCEPTANCE.json`, and hashes the complete manifest before atomic
promotion. It also requires and carries the two delivery markers, synthetic
edge-ingestion acceptance, hashed unused disposition, and finalization JSON
beneath `custody-evidence/`. It neither reads the plaintext secret tree nor
decrypts the escrow.
Do not substitute live volume copies. The restore test rechecks
hashes/manifest, restores into a unique Docker `--internal` network with no
published ports, verifies broker access with the read-only `command-audit`
identity, requires the retained-command probe's exact empty timeout result,
checks Influx metadata and the Node-RED Git Project/encrypted credential
artifact, and cleans up its isolated containers, volumes, network, and
temporary files.

Restore onto an isolated Docker network with all host ports blocked. Verify
hashes, broker configuration/persistence, Node-RED Git and credential
decryption, Influx metadata/query, and `PRAGMA integrity_check` on the ledger.
Connect test identities and run replay/command-negative tests before a
controlled production cutover. A restored broker must not emit an old
actuator command.

## Non-goals

- Moving Home Assistant authority, Matter Server, ESPHome, Music Assistant,
  simple/safety automations, final actuation, or HA backups out of HAOS.
- Running production applications on Proxmox or critical automation in
  `edcore-ops`.
- Public MQTT, Node-RED, InfluxDB, or event-harness endpoints.
- Recording raw audio, transcripts, credentials, exact personal-presence
  history, or arbitrary serial data.
- Replaying production actuator/control topics or operating dual-active
  brokers.
