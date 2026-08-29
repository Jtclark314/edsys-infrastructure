# 9950x EdSys Voice Gateway Deployment

**Status: retired 2026-08-29.** The former Home Assistant endpoint was
destroyed with EdCore. The installer, runtime initializer, and firewall helper
intentionally refuse to run until this stack is redesigned for a replacement
Home Assistant deployment.

This stack deploys the independent transcript-level Voice Gateway. It does not
receive raw audio and it is not part of the AI Portal process. Runtime secrets
live under `/etc/edsys-secrets/voice-gateway/` and never enter Git.

## Network boundary

- `127.0.0.1:8055` — host-local TLS API.
- `127.0.0.1:8056` — Prometheus metrics only.
- No wildcard host, Tailnet, Cloudflare, or public publication is defined.

The retired definition has no LAN publication. A future design must establish
a new source boundary before restoring remote access.

## Required private files

```text
/etc/edsys-secrets/voice-gateway/
|-- deploy.env
|-- gateway.env
`-- tls/
    |-- ca.crt
    |-- tls.crt
    `-- tls.key
```

The TLS certificate must contain `192.168.50.50` as an IP subject alternative
name. `tls.key` must be readable by container UID/GID `10001:10001` and by no
other unprivileged identity. The Home Assistant service identity must be
non-admin. Use the dedicated `edsys-voice-gateway` LiteLLM virtual key, limited
to the reviewed voice model aliases; never pass the OpenAI provider credential
to this stack.

## Install and activate

1. Build the reviewed private app source at an exact Git commit and put the
   resulting immutable Docker image ID (`sha256:...`) in private `deploy.env`.
   A private-registry `repo@sha256:...` reference may replace it later.
2. Populate private `gateway.env` from the example without printing values.
3. Install the internal certificate/key and CA.
4. Install the scripts and units:

   ```bash
   sudo scripts/install.sh
   sudo systemctl enable --now edsys-voice-gateway-firewall.service
   sudo systemctl enable --now edsys-voice-gateway-compose.service
   ```

`sudo scripts/init-runtime.sh sha256:<image-id>` can create a dedicated EdSys
internal CA, a LAN-IP-bound server certificate, private caller tokens, and a
fail-closed runtime skeleton. It never prints credentials and deliberately
leaves the HA, LiteLLM, and RAG service identities as `TO_BE_CONFIRMED`; the
preflight refuses to start until those are replaced with real scoped values.

5. Run `sudo scripts/verify.sh`, then exercise the application repo's text-only
   acceptance client.

The preflight refuses floating image tags, absent local image IDs, missing private files, certificates
without the LAN IP SAN, mismatched keys, expired certificates, and malformed
workflow policy.

## Home Assistant rollout gate

Do not select the custom conversation agent until text-plane acceptance passes.
Install the integration from the app repository, configure it with the dedicated
Home Assistant-to-Gateway bearer credential and trusted CA, validate through the
HA text conversation interface, and only then select it in `EdSys Hybrid Voice`.

On rollback, switch the HA pipeline back to the built-in agent first, then stop
this unit or restore the previous digest. Ephemeral conversation state is not
backed up or restored.

## Backup and privacy

The only durable material is Git-tracked source/policy plus encrypted private
secret escrow. The service has no conversation database. Logs and metrics must
contain identifiers, routes, timings, provider category, and status only—never
audio, transcripts, prompts, or response bodies.
