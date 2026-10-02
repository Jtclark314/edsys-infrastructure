# Google AI MCP setup checkpoint

Outcome sought: a local Codex tool for Gemini advisory reviews using Jeremy's
existing Google subscription, with no API-key billing or extra-credit fallback.

Completed on 2026-10-01:
- Prepared a stdio MCP bridge with installation/policy status and explicit-text review tools.
- Tested MCP initialization and tool discovery locally.
- Google's legacy Gemini CLI 0.62.0 rejected personal-account access with
  `UNSUPPORTED_CLIENT`; do not treat its successful OAuth sign-in as model access.
- Staged official Antigravity CLI 1.2.14 with verified release SHA-512 and pinned
  executable SHA-256; its account-based route is the replacement under test.
- Configured file, shell, web and nested MCP deny rules and disabled extra-credit fallback.
- Prepared the desktop login launcher. Secrets and private runtime stay outside Git.
- Repaired the launcher's handling of Antigravity's empty optional MCP configuration
  file. All five regression tests pass; the installed helper passes policy checks.
  A fresh native model-list check now succeeds after Antigravity sign-in.
- Completed account sign-in and verified available Gemini models and account quota.
- Live MCP `ask_gemini` returned the requested marker using `gemini-3.1-pro-high`.
- Direct provider attempts to read a canary file and execute a shell write were
  denied; no canary contents were returned and no write marker was created.
- Registered `edsys-gemini` in Codex with a 150-second tool timeout, preserving
  unrelated configuration. The reproducible pinned installer passes live checks.

Next owner action: open a fresh Codex chat to load the configured tools.

No provider blocker remains. The exact subscription-tier label and discovery in
a newly opened Codex chat remain to be confirmed. Native account access, quota,
MCP transport and a real Gemini response are verified. No hub restart is required.
