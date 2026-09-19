# Qualification

These checks use disposable local fixtures. Keep outputs and agent transcripts
under `/mnt/ai-store/local-coder/evidence/`, never in Git. Unit tests require
the pinned desktop Python environment; the network/model checks require the
installed runtime. They make no external business-system writes.

1. Run `python -m unittest discover -s tests -v` with the desktop venv.
2. Run `tests/qualify_model.py` with Python for native thinking/tool roundtrips.
3. Run `tests/qualify_desktop.py` with the desktop venv for MCP image, keyboard
   and rejected-input checks.
4. Run `tests/prepare_fixture.py` once with Python. It refuses to replace an
   existing coding fixture. Preserve prior evidence before preparing a new one.
5. Use `edsys-code --tools code run --dir
   /mnt/ai-store/local-coder/qualification/coding` to ask the model to delegate
   inspection of `parse_duration`, fix it to the README contract, add edge-case
   tests, run unittest and report. Approve only this disposable scope. Verify
   actual tool traces and run `python3 tests/verify_fixture.py` independently.
6. Serve the disposable `qualification/web` folder with `python3 -m http.server
   18765 --bind 127.0.0.1 --directory /mnt/ai-store/local-coder/qualification/web`.
   Ask `edsys-code --tools browser` to navigate there, type a unique marker in
   Verification note, click Save note and verify the rendered result with a
   snapshot and screenshot. Do not accept file/JavaScript bypasses as UI proof.
7. Ask `edsys-code --tools desktop` to open the scratchpad, inspect a screenshot,
   click/type a unique marker, click Save note and inspect another screenshot.
   Verify the actual private `desktop-notes/note.txt` matches the marker. Do not
   supply target coordinates or accept shell/file tools as visual-control proof.

Record interventions, failures and retries. A passing toy task is a wiring and
bounded capability check, not a broad autonomous-coding benchmark. Once active
model requests finish, restart only `ollama-coder` and verify cold inference,
tool output, model/context persistence, resource limits and unrelated health.

## EdSys context and terminal qualification

Run the focused suite in `test_knowledge.py` through the pinned Python runtime.
It covers read-only/schema/freshness/source-drift boundaries, history exclusion,
unknown queries, bounded pages, checkpoint isolation/revision conflicts/evidence
changes, credential exclusions and command construction. `install-context.sh`
runs the whole local-coder unit suite and `test_context_plugin.mjs` before a
controlled web reload. The hook tests cover refreshed context on successive,
child and compaction calls, plus failure when the project is unavailable.
Run `python3 tests/qualify_context_injection.py` against the installed client
to capture real OpenCode parent/child/continuation requests with MCP disabled.
That test uses a local fake provider; it verifies injection, not model quality.

For model acceptance, use a disposable Git project with a distinct root AGENTS
marker, a small broken function and tests. With the normal profile (no `--auto`),
ask Qwen to fix it, delegate a read-only EdSys ownership lookup, run tests and
finish its normal continuity workflow. Inspect actual tools for child retrieval,
edits, commands and checkpoint save; independently run
edge cases. Then open a fresh web session in the same project and request recall
without copying the old conversation. Include a delegated context/unknown-device
check, verify no fabricated device and no shared checkpoint writes by children.
Separately disable tools, save a new checkpoint marker outside the conversation,
and verify a fresh model turn recalls that marker, revision and evidence status.
Run explicit conversation compaction, change the checkpoint marker again, and
verify the resumed model sees the new context while retaining the EdSys briefing.
This distinguishes automatic injection from old conversation recall or an
optional context-tool call. Record interventions and incorrect
citations rather than accepting a plausible summary as proof.

PowerShell acceptance uses a uniquely named temporary file with Unicode, quotes
and dollar signs, checks its exact content and removes it in a finally block on
9950x, Nimo and Basecamp. A separate `exit 7` script must return exactly 7 on all
three routes. The maintained Windows SSH default shell is PowerShell and needs
explicit outer exit propagation. No production file is edited by these checks.
Verify the web worker can use existing sudo with `sudo -n id -u`; this read-only
check does not grant new OS permissions. Unknown/offline endpoints are explicit
gaps, not grounds to weaken host verification or change credentials.
