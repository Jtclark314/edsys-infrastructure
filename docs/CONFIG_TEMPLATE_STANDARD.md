# Config Template Standard

Status: starting baseline.

Config templates should make deployment easier without leaking private values.

## Template Rules

- Use placeholders such as `CHANGE_ME`, `example.invalid`, or `${VARIABLE_NAME}`.
- Never include real tokens, passwords, private keys, API keys, tunnel IDs, or VPN credentials.
- Explain where real values come from.
- Prefer comments that describe intent over copied local state.
- Mark host-specific values as `to be confirmed` unless verified.

## Examples

- Safe: `API_TOKEN=CHANGE_ME`
- Safe: `CF_TUNNEL_TOKEN=provided locally outside Git`
- Unsafe: any real token or credential value.
