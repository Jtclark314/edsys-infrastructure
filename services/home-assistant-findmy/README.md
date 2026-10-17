# Find My Linux pilot

Prepared on 9950x, October 17, 2026. Linux exporter build and isolated checks
pass. Apple authentication, real key export, HA installation and tracker
freshness remain pending. The owner could not recall the Apple password;
the current exporter has no QR/passkey sign-in flow. No HA files, integrations,
automations, media or voice settings have been changed by this pilot.

## Sources and ownership

- Exporter: `stek29/export-findmy`, commit in `pins.json`, with
  `exporter-privacy.patch`. The checked-in Cargo lock pins transitive sources.
- HA candidate: `malmeloo/hass-FindMy`, commit in `pins.json`, with
  `hass-findmy-privacy.patch`; its Python dependency is FindMy 0.10.2.
- Build definitions, patches, launcher and checks belong here. All downloaded
  source, binaries, images, logs, profiles, credentials and key material remain
  outside Git. `pins.json` contains public provenance only.
- HA remains authority for entities and future automations. This is a one-tag
  trial; successful import alone does not establish reliable presence.

## Current isolated build

Private task root:
`/home/jeremy/.local/share/edsys-findmy/2026-10-17` (0700).

The builder runs as UID 1000 with four CPUs, eight GiB, dropped capabilities,
no privilege escalation, a read-only root, and only public source/build mounts.
Dependency fetch occurs separately; compilation and all 15 upstream tests ran
with `--network none --offline --locked`. Rust is 1.99.1. Host Rust, Python,
apt packages and existing services were not changed.

To reconstruct in a new private build directory:

1. Clone the exporter and HA repositories and check out the full commits in
   `pins.json`. Verify each `git rev-parse HEAD` before applying its patch.
2. Apply the matching patches using `git apply --unidiff-zero`; copy this `Cargo.lock` into the
   exporter checkout. Do not generate a new dependency lock during a rebuild.
3. Build `Dockerfile.builder`. Mount only a private build directory at `/build`
   and public source read-only at `/source`, with user 1000:1000 and the limits
   above. Copy source to `/build/export-findmy`. Run `cargo fetch --locked` in
   a network-enabled fetch container; then use a new network-denied container
   for `cargo build --release --offline --locked` and
   `cargo test --release --offline --locked`.
4. Make a small image context containing `Dockerfile.runtime` as `Dockerfile`
   and the resulting binary as `export-findmy`; build the runtime image. Never
   use a credentialed directory as a Docker build context.
5. Verify binary/runtime digests, `--help` without network, and private input
   checks before reviewing any new image pin in `export-private.sh`.

Base images are digest-pinned. Debian package repositories are used during
image construction; an image rebuild is not promised to be byte-identical.
The installed launcher pins the exact runtime image that passed the checks.

## Private sign-in

The installed command is:

```sh
/home/jeremy/.local/bin/edsys-findmy-export
```

Run only in the owner's actual 9950x terminal, such as Moonlight or SSH. Do not
launch it through a Codex-recorded PTY, capture its screen, redirect its output,
or request credentials in chat. `--check` runs only offline help. Normal use
requires a TTY, disables core dumps and Rust logging, and uses Docker's `none`
log driver. The patched input code checks terminal echo operations and hides
password, verification code and device passcode. Non-TTY secret input fails.

The wrapper serializes export attempts with a lock, sets umask 077, uses only
the private export directory, and stops its local anisette helper when done.
There are no scheduled runs or automatic login retries.

The self-hosted, digest-pinned Dadoum anisette-v3 helper is named
`edsys-findmy-anisette` on the dedicated `edsys-findmy-pilot` Docker network,
alias `anisette`, internal port 6969. It has **no published host port** and no
restart policy. Its libraries/provisioning state are under `private/anisette`;
temporary provisioning uses tmpfs. It runs without capabilities or retained
Docker logs. It downloads libraries/provisions directly with Apple. The
exporter explicitly uses this helper rather than its upstream public default.

To recreate that helper, use the digest in `pins.json`, UID 1000:1000, read-only
root, a 128 MiB `/tmp` tmpfs, `XDG_RUNTIME_DIR=/tmp`, the private anisette directory
mounted at `/home/Alcoholic/.config/anisette-v3`, one CPU, 768 MiB, pids 128 and
`--log-driver none`. Start with no port publication. Verify
`/v3/client_info` inside the container before sign-in.

## Account effects and sensitive state

This is an unofficial account/keychain client, not a read-only website login.
It authenticates with Apple, recovers trusted-device escrow using that device's
passcode, joins iCloud Keychain as a synthetic peer and creates an escrow
recovery record. The copied private profile has a consistent iPhone preset and
stable generated identifiers. Never guess a device passcode or repeatedly retry.

The exporter retrieves **all paired accessories** and may synchronize broader
encrypted Keychain records from Manatee/ProtectedCloudStorage, not merely tag
keys. Treat its entire private export directory as Keychain-sensitive. Profile,
keystore, keychain state, anisette state and accessory files must not enter Git,
Obsidian, RAG, chat or unreviewed backups. Owner-only permissions are not disk
encryption. Credentialed Rust logs stay off because a dependency can log key
material at INFO level. The wrapper disables the optional auth cache.

The upstream escrow deletion command can delete recovery records and can list
real devices. It is not exposed by the normal wrapper. Do not run it blindly:
removing the synthetic recovery record does **not** prove the trusted peer was
revoked. Retain state privately while the pilot is pending; review account-side
cleanup separately before deleting recovery material or declaring retirement.

## One-tag selection and HA preparation

After a successful export, `select-tracker.py` lists tag names only in the
owner's terminal and stages exactly one JSON file at
`private/selected-tracker.json` (0600, exclusive creation). It checks key sizes,
identifier, pairing/alignment dates and index and uses the real pinned library
parser. Existing selections are not overwritten. Missing secondary keys or
alignment stop the trial; do not invent cryptographic fields or silently scan
years of rotating keys.

Prefer JSON: it preserves the current key alignment and tag name. HA's plist
upload path loses some of that metadata. Exported files remain secrets.

HA 2026.10.2 and Python 3.14.6 were observed live. The component candidate has not
been installed. Before installation, capture a fresh protected native backup
and narrow current files/registries, recheck the two parallel HA tracks and
preserve active voice 0.6.0/staged 0.6.1. Install only this component, not unrelated
older source packages. Check configuration and use one normal authorized Core
restart if needed. Verify exact component hashes and native flow availability.

The privacy patch redacts verification codes and static keys in debug forms.
It saves account session tokens with `account.password=None`; token expiry
opens HA reauthentication rather than retaining the raw Apple password. Tokens
and tag private keys still live in HA configuration and protected backups.
Do not enable dependency debug logs or auto-update over these patches.

Use the native FindMy account flow with the integrated anisette provider
(leave its optional URL blank). The exporter's V3 helper URL must not simply
be copied into HA's different remote-anisette protocol. Use native file upload
for the selected JSON and choose rolling-derived device type. Keep the original
iCloud3/phone tracking unchanged. No browser credentials or other app tokens
are extracted or repurposed.

Cloud polling is normally 15 minutes; polling time is distinct from the source
location timestamp. Verify a real returned timestamp and location privately,
then a later fresh observation. Bluetooth proximity needs an actual HA adapter
or ESPHome proxy, tag compatibility and a separate arrive/leave test; no receiver
or physical acceptance is claimed. Keep automations disabled until those tests.

## Verification and recovery

- Exporter:15 upstream tests; offline runtime help; exact patched input functions
  exercised with synthetic PTY input (hidden) and non-TTY input (refused).
- HA patch:7 offline checks using real FindMy 0.10.2/CPython 3.14.6, exact modified
  methods with small HA framework fakes, and socket connections denied.
  Token-only restart restoration, HTTP 200 fetch, HTTP 401 reauthentication, both
  account save paths, log redaction and synthetic accessory derivation pass.
- All 12 component modules compile. This does not prove live native-flow,
  Apple-account, real-tag or physical-detection acceptance.

Run packaged checks with `FINDMY_COMPONENT_DIR` pointing to the patched
component and an isolated Python3.14 environment using `requirements-check.lock`:

```sh
FINDMY_COMPONENT_DIR=/PRIVATE/PATCHED/custom_components/findmy \
  /PRIVATE/VENV/bin/python test_ha_privacy.py
bash -n export-private.sh
```

Stopping the helper/launcher does not alter existing HA. No listener or boot
service was added. Build caches and public source are reconstructible. Preserve
private account recovery state until account-side cleanup is understood. After
future HA installation, disable/remove only the pilot entries and component;
avoid restoring an entire HA backup over newer work from other tracks.

Primary references: [exporter](https://github.com/stek29/export-findmy),
[FindMy](https://github.com/malmeloo/hass-FindMy),
[Linux key-export development](https://github.com/malmeloo/FindMy.py/issues/173),
[anisette helper](https://github.com/Dadoum/anisette-v3-server).
