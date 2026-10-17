# Find My Linux pilot

Prepared on 9950x, October 17, 2026. Linux exporter build and isolated checks
pass. A subsequent private sign-in exposed a local helper URL defect before
Apple password verification; that fix exposed Apple SRP error -80045. A
Windows compatibility build now reaches structured Apple responses after the
Android candidate returned HTML. The latest build adds a private password-free
initial-request diagnostic and corrects secret whitespace handling. Real exporter
authentication, key export, HA installation and tracker freshness remain pending.
The current exporter has no QR/passkey sign-in flow. No HA files, integrations,
automations, media or voice settings have been changed by this pilot.

## Sources and ownership

- Exporter: `stek29/export-findmy`, commit in `pins.json`, with
  `exporter-privacy.patch`, then `exporter-linux-identity.patch`. The checked-in
  Cargo lock pins transitive sources.
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
2. Apply `exporter-privacy.patch` then `exporter-linux-identity.patch` to the
   exporter using `git apply --unidiff-zero`; apply the HA privacy patch to its
   matching checkout. Copy this `Cargo.lock` into the
   exporter checkout. Copy `apple-private-apis` from the exact recursively checked
   out RustPush commit in `pins.json` to `vendor/apple-private-apis` under the
   exporter. Apply `omnisette-transport.patch` there with `--unidiff-zero`, and
   apply `icloud-auth-response.patch` there too, and append
   `Cargo.local-patch.toml` to the root exporter `Cargo.toml`. This local
   override keeps the reviewed dependency source patched without mutating a
   shared/global Cargo cache. Do not generate a new dependency lock in a rebuild.
3. Build `Dockerfile.builder`. Mount only a private build directory at `/build`
   and public source read-only at `/source`, with user 1000:1000 and the limits
   above. Copy source to `/build/export-findmy`. Run `cargo fetch --locked` in
   a network-enabled fetch container; then use a new network-denied container
   for `cargo build -p export-findmy --release --offline --locked`,
   `cargo test -p export-findmy --release --offline --locked`, and
   `cargo test -p omnisette --features remote-anisette-v3 --release --offline
   --locked transport_tests`, plus `cargo test -p icloud_auth --lib
   --features remote-anisette-v3 --release --offline --locked`. The upstream `test_adi` requires live Apple
   provisioning and is intentionally excluded from credential-free checks.
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
There are no scheduled runs or automatic login retries. Helper startup waits
for local `/v3/client_info` readiness before asking for credentials: 20 attempts,
each with a one-second curl limit and 0.25-second pause (roughly 25 seconds max).
`--check-helper` performs only that local readiness check, discards the response
body and stops the helper afterward; it never starts the Apple account flow.
`--check-signin` is different: run it only in the owner's private terminal. It
asks for the Apple ID and sends one initial SRP request to Apple, without a
password proof, 2FA, delegate fetch, Keychain join or tracker export.

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

## Local helper transport repair — October 17, 2026

The owner observed `ErrorGettingAnisette(WsError(Url(UnsupportedUrlScheme)))`
immediately after the password prompt. Dependency source confirms anisette
provisioning precedes Apple SRP password verification. It mapped only HTTPS to
WSS, leaving our local HTTP helper address as an invalid HTTP WebSocket URL.

The pinned local dependency patch maps HTTP→WS and HTTPS→WSS using parsed URLs,
preserves base paths/trailing slashes, and rejects malformed/non-HTTP schemes
with sanitized errors. Three offline tests include a real loopback WebSocket
handshake using the exact generated URL. All 15 exporter tests also pass. The
built binary hash inside the new pinned image matches the checked release
artifact. Independent review and installed launcher hash comparison pass.

A full workspace test attempt also included upstream `test_adi`; its external
Apple lookup was blocked by the network-denied container. No credentials were
used. The focused offline suite excludes that external test. Lock changes are
limited to the local Omnisette/ClearADI identities and two test dependencies;
previous registry/Git dependency versions are unchanged. Existing private
profile, provisioning and keychain state were preserved, with no account retry
performed by Codex. The owner was given the same command for one private retry.
Successful account authentication/export remains unverified.

## Apple SRP identity candidate — October 17, 2026

The next owner screenshot showed `AuthSrpWithMessage(-80045, "This action cannot
be completed at this time.")`, confirming progress beyond the URL-scheme bug.
This error does not establish an incorrect password. The merged upstream
[xtool identity fix](https://github.com/xtool-org/xtool/pull/337) associates this
error with Android ADI data presented under an Apple-device identity. Its
[lookup refinement](https://github.com/xtool-org/xtool/pull/348) remains open;
our adaptation is a tested local candidate, not verified account acceptance.

`exporter-linux-identity.patch` uses the matching Pixel 7/Android 14 AuthKit
identity for GSA sign-in and helper provisioning, retaining existing service
capabilities. Only URL-bag discovery uses a desktop identity. Real request
builder tests prove that this is one replaced header, not two appended values.
The provider adapter restores the original iPhone base client-info for existing
delegate/CloudKit/escrow paths; real GSA composers explicitly choose Android.
Opaque authentication fields are preserved. Existing private profile, trust,
recovery and prior anisette state are not reset; new helper state uses the
separate `anisette_state_android_v1` directory.

That first candidate printed `20261017-android1` and pins the exact new image/binary in
`pins.json`. All 18 exporter checks (15 upstream plus 3 identity regressions)
and 4 transport/header checks pass with networking denied. Image digest/binary
hash, offline help and local readiness pass. The same binary's
`--check-anisette` completed provisioning and header retrieval with a disposable
synthetic profile and no Apple account credentials, then exited before login.
The wrapper does not expose that diagnostic or alter normal 2FA retries.

Synthetic GSA init and actual-runtime dummy-account probes returned an HTML
error page rather than a usable Apple authentication result. They are
inconclusive for account acceptance. Only generated dummy identity/credentials
were used; no owner account was signed in by these checks, and no diagnostic
state entered Git. A single private owner retry remains necessary. Do not
claim password, 2FA, Keychain, real export or HA acceptance from software tests.
No automatic account retry, identity reset, escrow deletion or HA write occurs.

## Current Windows compatibility build — October 17, 2026

The owner confirmed the Android candidate failed with an HTML/plist decoding
error. A credential-free control reproduced HTTP503/HTML for Android metadata;
Windows metadata reached the service. Current primary reports in
[anisette issue61](https://github.com/Dadoum/anisette-v3-server/issues/61) and
[iPASide1.2.8](https://github.com/pwnapplehat/iPASide/blob/main/CHANGELOG.md)
describe the same changing Apple behavior and Windows compatibility route.

Build `20261017-windows1` now uses Windows/AuthKit metadata for GSA and helper
provisioning, Windows/AOSKit for account delegates, and Windows/sbd for escrow.
A separate same-type provider preserves the original iPhone CloudKit context;
both providers read the same stable `anisette_state_windows_v1` identity. The
existing device profile/peer, old provisioning and recovery files are preserved.
No get_account_settings call exists in this pinned exporter path. No generic
RustPush override or cryptographic implementation change was needed.

The local `icloud_auth` patch reports fixed stage/status/response-kind errors
for HTML or malformed GSA/verification replies, preserving structured Apple
errors. Successful SRP completion with `hsc=409` goes directly to private code
entry without requesting a second code. Existing explicit repair/2FA handling
remains authoritative. GSA connections close after each request; there is no
automatic account retry. Raw SMS response logging is removed.

All 20 exporter, 22 auth library and 4 transport/header tests pass offline with
network denied. The auth library suite includes 12 new HTTP/2FA regressions.
Its old live `gsa_auth` integration example is excluded: it has an outdated
closure signature and requires an account. No such live account test ran.
The local auth manifest points SRP back to the exact locked Git source, avoiding
unrelated nested-workspace dev dependencies. No package versions changed;
only `icloud_auth` changed from the pinned Git source to its reviewed local copy.

The exact runtime image/binary hashes in `pins.json` match. Fresh anonymous
helper provisioning/header retrieval passes. A synthetic SRP-init probe gets
HTTP200 and structured Apple -20970; the actual new binary, with a generated
nonexistent account and dummy input only, gets the expected "Your account
information was entered incorrectly" rejection. This proves progress beyond
HTML/-80045 for the synthetic case; it does not prove owner password, 2FA,
delegate, escrow, real export or HA acceptance. No real account was used in
diagnostics. The installed launcher help and local readiness checks pass.

Owner was given the unchanged private terminal command for one retry, with
`20261017-windows1` required in its opening text. No HA file/restart/device
change or scheduled continuation occurred. A later real export still needs
one-tag validation, protected HA backup and independent HA auth qualification.

## Password-free sign-in diagnosis — October 17, 2026

Build `20261017-windows2` keeps the existing Windows metadata, helper identity,
SRP implementation and endpoint. Apple error -20209 from this client does not
by itself prove that all Apple sessions or official web sign-in are blocked.
Do not automatically prescribe another password reset or an app-specific
password; this exporter uses the primary-account SRP/2FA flow.

The installed source had two concrete defects: secret input used `trim()`,
which changed significant leading/trailing whitespace, and structured Apple
errors discarded the known request stage. Secret reads now remove only one
terminal LF/CRLF, preserving other whitespace. GSA errors expose only fixed
stage, HTTP status and Apple error code; no response body or server message.
No password-calculation defect explaining -20209 was established.

Run the next check once, in the owner's private terminal:

```sh
bash /home/jeremy/.local/bin/edsys-findmy-export --check-signin
```

It must display `20261017-windows2` and asks only for Apple ID. The diagnostic
shares the exact initial request path with real login and stops after the
initial response, before cache/keystore/output handling. An incomplete device
profile is refused without generating or rewriting peer identifiers. The local
helper can provision/update its own authentication state if needed. It sends a fresh public
SRP ephemeral, never a password proof or completion request. A positive result
proves only that Apple accepted this initial request; password and 2FA remain
untested. A rejection reports stage/status/code. Account-specific requests can
still be subject to Apple's rate/risk controls; there are no automatic retries.
The wrapper stops its helper afterward and does not run tracker selection.

All 52 offline checks pass: 23 exporter, 25 auth library and 4 transport/header.
Focused regressions include secret whitespace and CRLF cases, exact
shared init-only request serialization, and synthetic -20209 responses at init
and complete. No real owner account or additional live account probe is used
by Codex. Existing private state is retained; HA remains untouched.

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

Separately verify current HA authentication compatibility; this exporter-only
identity repair does not patch FindMy.py. Use the native FindMy account flow
with the integrated anisette provider
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

- Exporter:20 checks (15 upstream plus 5 identity), 22 auth library checks,
  4 transport/header checks;
  offline runtime help; exact patched input functions
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
