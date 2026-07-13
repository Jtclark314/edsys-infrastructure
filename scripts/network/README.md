# 9950x AI Tailnet Socket Proxy

The remote-capable AI services on `9950x` publish their Docker ports directly
only on loopback and `192.168.50.50`. Eight systemd socket instances expose the
same ports on the exact Tailnet address `100.87.137.47` and forward each
connection to its LAN-bound peer:

| TCP | Service |
| ---: | --- |
| 3000 | Open WebUI |
| 3002 | AnythingLLM |
| 6333 | Qdrant HTTP |
| 7997 | Infinity embeddings |
| 8015 | EdSys Glasses Gateway |
| 8020 | EdSys AI Gateway |
| 8099 | EdSys Control API |
| 11434 | Ollama |

`FreeBind=yes` lets the socket layer bind the exact Tailnet address before
`tailscale0` exists during boot. Docker therefore never depends on that dynamic
interface and cannot fail its `unless-stopped` recovery merely because
Tailscale is late or unavailable. A socket accepts traffic only on the exact
Tailnet IP; there is no wildcard listener. If the LAN-bound container is down,
the proxy cannot reach its target and fails closed without exposing another
interface.

The template is not an unrestricted forwarding primitive. A root-owned marker
must exist for the instance port, and the installer creates markers only for
the eight reviewed ports. Piper `10200`, Whisper `10300`, and OpenWakeWord
`10400` remain Docker-published only on loopback and LAN and never receive
Tailnet proxy instances.

## Controlled deployment

Validate and install the units without starting listeners:

```bash
sudo scripts/network/install-9950x-ai-tailnet-proxy.sh --install-only
```

Then remove the direct `100.87.137.47` publication from each tracked Compose
service and recreate one service at a time with its already-approved image or
local build. Do not run a broad pull or rebuild. After the Docker listener for
each reviewed service is loopback/LAN-only, enable all proxies:

```bash
sudo scripts/network/install-9950x-ai-tailnet-proxy.sh --enable
sudo /usr/local/sbin/edsys-ai-tailnet-proxy-check
```

The checker requires exactly three host listeners for each remote-capable
port--loopback, LAN, and the systemd-owned Tailnet socket--and exactly two for
each voice port--loopback and LAN. It also proves that every socket is enabled,
active, configured with `FreeBind=yes`, and can reach its LAN target.

## Boot and client acceptance

After a full host reboot, run the checker before declaring boot recovery
accepted. Also verify local dependencies and service-specific health, then test
the Tailnet URLs from Nimo and Basecamp. A voice-port probe from a Tailnet-only
client must still fail. `systemctl status
edsys-ai-tailnet-proxy@3000.socket` is the model for inspecting one instance.

## Rollback

Every installer run writes root-private prior files and unit state under
`/var/backups/edsys-ai-tailnet-proxy/<UTC timestamp>/`. To remove the proxy
layer without deleting application data:

```bash
for port in 3000 3002 6333 7997 8015 8020 8099 11434; do
  sudo systemctl disable --now "edsys-ai-tailnet-proxy@${port}.socket"
done
```

Restore prior unit files only from the intended private rollback directory,
run `systemctl daemon-reload`, and restore direct Tailnet Compose publications
only if the Tailnet address is already present and the cold-boot dependency is
explicitly accepted. Never delete Docker volumes or application state as part
of this network rollback.
