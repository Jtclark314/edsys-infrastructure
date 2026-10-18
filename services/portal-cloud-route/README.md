# Portal-scoped cloud route

Owns only the new `edsys-portal-openai` broker alias and append-only access for
the existing `edsys-ai-portal` service credential. It reuses the existing broker
OpenAI connection, targeting `openai/gpt-6.1-sol`, without exporting credentials.
No other model, global alias/fallback, client policy, HA route or AnythingLLM
workspace/allowlist is changed. In particular `edsys-chat-cloud` remains intact.

The Portal application owns consent, compatible payloads, a single 60-second
cloud attempt, one explicit local fallback within a shared 120-second deadline,
and truthful actual-model/fallback metadata. This tool runs no inference or
voice/golden evaluation. The formal judge still requires separate credential/
access qualification and explicit resumption; its timer stays paused.

```bash
python3 -m unittest discover -s services/portal-cloud-route -p 'test_*.py' -v
python3 services/portal-cloud-route/provision.py --private-state /PRIVATE/portal-cloud-route/broker
```

With authorized implementation, append `--apply` only after reviewing the private
read-only preflight. The tool records its attempt before writes, compares hashes
of all existing models/client policies, and verifies that only the new model and
Portal's appended model allowlist changed. It refuses replay after any attempt,
including partial failure. Inspect the private receipt and live state instead;
do not use a fresh state directory to bypass the guard.

Recovery order: restore the Portal's prior image first, which reads the unchanged
legacy catalog and does not select this alias. Preserve current application
history/data and existing private environment/bindings. Retain the harmless alias
and appended permission until dependency review confirms removal is safe. Later
removal must delete only this alias and its single Portal allowlist entry via the
native broker API, preserving the existing key and all prior models. Never
restore the whole broker database or unrelated clients to undo this change.
Private preflight/result receipts and deployment/history checkpoints need the
existing protected backup path; credentials/raw runtime exports never enter Git.

Portal source: `/home/jeremy/code/edsys-ai-portal/app/portal_cloud.py`, catalog
migration, native consent checks and `tests/test_portal_cloud.py`.
