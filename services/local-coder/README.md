# Local Qwen coding agent

Owner: Jeremy. Host: 9950x. Runtime state and qualification evidence are under
`/mnt/ai-store/local-coder/`, outside Git. High-level acceptance belongs in
`EdSys-Master/docs/LOCAL_CODING_AGENT.md`.

## Components

- Official Qwen3.6-35B-A3B Q8_0 weights, pinned by manifest and full weight hash
  in `artifacts.json`. Ollama calls the architecture 36.0B including its other
  components; this is the requested 35B-A3B family, not another model.
- Ollama 0.33.2 at the existing reviewed image digest; a separate
  `ollama-coder` service publishes only `127.0.0.1:11435`. Its internal broker
  address is `http://ollama-coder:11434` on the existing workhorse network.
- CPU-only inference: 12 CPU quota/threads, 48 GiB memory ceiling, no swap,
  one active request/model, four queued requests, and two-minute idle unload.
  No GPU device is admitted. Root filesystem and shared model store are
  read-only, capabilities are dropped, and privilege escalation is disabled.
- `edsys-qwen36-coder:latest` is the local configured alias: 65,536 context,
  16,384 maximum generated tokens, Qwen's precise-coding sampling settings,
  and preserved upstream vision/tool/thinking behavior. Here `latest` names
  the local alias; weights and engine are explicitly pinned.
- OpenCode 1.18.31 and Playwright MCP 0.0.81 are locked in the npm manifest.
  Python MCP 2.2.0 and Pillow 12.3.0 plus dependencies use `requirements.lock`.
- OpenCode model, auxiliary model, and subagents all use the local provider.
  Reasoning content is preserved across calls. Sharing and automatic upgrades
  are disabled; the launcher uses private XDG state and disables project config
  overrides, external plugins, remote model-catalog fetches, and inherited OTEL
  exporters. Repository instruction files still apply.

## Use

```bash
cd /path/to/your/project
edsys-code
edsys-code --tools code
edsys-code --tools browser
edsys-code --tools desktop
edsys-code --tools code run 'Inspect this bug and suggest a fix'
```

The default `all` profile enables both MCPs. Narrow profiles save context when
the task only needs code, browser, or desktop tools. Code edits and delegation
are enabled; shell commands and interactive browser/desktop actions ask for
approval by default. Do not use automatic approval on an unreviewed repository
or with consequential external actions. Tool permissions are workflow controls,
not an OS sandbox.

Playwright uses a separate ephemeral, headless Chrome profile with its browser
sandbox enabled. The desktop MCP starts a separate Xvfb display at `:90` or
higher, protected by Xauthority, with a private D-Bus session and Xfwm4 window
manager. Screenshots are 1280x800; click/drag coordinates use Qwen's normalized
0–1000 scale on each axis and are converted to bounded screen pixels. It supports click/double-click, type, keys,
scroll, drag, and launching Chrome, Mousepad, a calculator, or the scratchpad.
The operator's physical `:0` and XRDP `:10` sessions are not targeted. These
processes run as Jeremy: separate desktop/browser sessions do not provide
filesystem or network isolation. Screenshots and working notes stay private.

The existing LiteLLM broker also exposes `edsys-coder-agent-local`. Its dedicated
service-scoped credential is stored outside Git under
`/opt/edsys-workhorse/litellm/service-keys/edsys-local-coder.env` with root-only
permissions. Existing client allowlists and model aliases are preserved. The
direct local launcher requires no broker credential. Broker credentials can be
reissued through the existing service-key workflow if restoring from source.

## Install and reproduce

Run `./install.sh` only for an authorized install or recovery. It requires
mounted AI Store, the pinned Ollama image, the existing shared Ollama service,
the existing broker network, and the listed host GUI tools. The installer pulls
the named source model and fails closed if its official manifest has drifted,
verifies the complete weight checksum, creates the alias, installs locked
clients, and starts only this Compose service. It does not upgrade shared
Ollama, change Codex, or select a cloud provider.

The shared Ollama store holds one copy of the 38.7 GB weights. The dedicated
server mounts it read-only. Do not remove the source tag, alias, or referenced
blob during unrelated model cleanup. A model upgrade requires a deliberate
artifact-lock review and renewed tool/vision/coding qualification.

## Verify and recover

```bash
python3 verify_model.py --full
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml ps
curl --fail http://127.0.0.1:11435/api/version
docker exec ollama-coder ollama show edsys-qwen36-coder
docker exec ollama-coder ollama ps
edsys-code --version
edsys-code mcp list
/mnt/ai-store/apps/local-coder/desktop-venv/bin/python -m unittest discover -s tests -v
```

The nonblocking applications tier in the ordered recovery manifest includes
this service and its loopback API gate. Docker's existing storage mount guard
and `unless-stopped` policy apply. Full-host reboot acceptance is separate from
the controlled service restart check; do not reboot production just to test it.

If inference fails, inspect `docker logs --tail 80 ollama-coder`, host available
memory, and the private OpenCode log under `data/opencode/log/`. A healthy API
alone does not prove useful model output. CPU prompt processing and generation
are bounded by hardware; changing the 64K context or concurrency requires fresh
memory and agent tests.

## Rollback and backup

Stop only this service with `docker compose -f compose.yaml stop`; preserve
weights and private session data for diagnosis. Remove its recovery-manifest
entry if intentionally disabling the service. The existing shared model routes
remain the previous working baseline. Restore the launcher/config from Git and
use the artifact/dependency locks for rebuilds.

Weights, npm/Python environments, and browser caches are replaceable downloads;
they do not require backup as unique source. Source/configuration are protected
by Git. Conversations, snapshots, screenshots, and desktop notes are private
local working state, not part of Git/Obsidian/RAG; their off-host backup remains
to be confirmed. Do not copy raw qualification logs into documentation.

## Upstream references

- [Qwen model and sampling guidance](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [Official Q8 artifact](https://ollama.com/library/qwen3.6:35b-a3b-q8_0)
- [Ollama tool loops](https://docs.ollama.com/capabilities/tool-calling)
- [OpenCode provider configuration](https://opencode.ai/docs/providers/)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)
