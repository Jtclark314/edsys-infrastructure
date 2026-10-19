# Hugging Face current Hub tools

This host connection restores public model, dataset, paper and documentation
discovery using Hugging Face's current official MCP interface. It complements
the account-connected plugin; it does not replace its private-repository or
Jobs authentication.

The 2026-10-18 connection check found that the installed plugin advertises
`model_search` and `hf_doc_search`, while its upstream no longer lists those
names. The current official anonymous endpoint advertises `hub_repo_search`,
`hub_repo_details`, and `hf_fs`. A real model search through `hub_repo_search`
returned a public model successfully.

## Install

Use the accompanying sanitized MCP configuration example in the private host's
Codex config. Keep the explicit three-tool allowlist. The connection has no
credential, inference, repository-write, Jobs, sandbox, or dynamic-Space tool.
No listener or new local service is required.

The existing authenticated plugin remains installed. This connection is for
public discovery and content; it has anonymous rate limits. Do not route
private repository requests to it or infer private access from public search.

Codex clients sharing this host load the connection after MCP configuration
reload or in a new chat. Verify `tools/list`, then search one public model.

## Recovery

Remove only the `huggingface-hub` block to roll back. Keep unrelated connection
settings and account credentials. No app reinstall or permission-mode change is
needed. If the hosted plugin later supports the current tool names, reassess
whether this separate discovery connection is still useful.

References:

- https://huggingface.co/docs/hub/agents-mcp
- https://github.com/huggingface/hf-mcp-server
- https://developers.openai.com/codex/mcp
