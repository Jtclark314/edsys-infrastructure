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
