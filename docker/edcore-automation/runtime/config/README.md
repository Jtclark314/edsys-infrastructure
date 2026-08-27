# Command authorization policy

The tracked policy starts with an empty allowlist, so the runtime is healthy
but no production command can pass. Add only a real, reviewed Home Assistant
target/action pair after its non-production replay and acknowledgment path
passes.

Example syntax (illustrative only; do not copy it as authorization):

```json
{
  "schema": "edsys.command-policy.v1",
  "max_ttl_seconds": 300,
  "allowed": [
    {
      "target": "ha/example-domain/example-entity",
      "action": "example-action",
      "parameters": {
        "required": ["level", "mode"],
        "properties": {
          "level": {"type": "integer", "minimum": 0, "maximum": 100},
          "mode": {"type": "string", "max_length": 8, "enum": ["normal", "quiet"]}
        }
      }
    }
  ]
}
```

Targets and actions are exact matches. Wildcards and additional parameters are
not supported. Every future allow rule must carry an exact scalar schema:
booleans, bounded integers/numbers, or bounded string enums. Authority redirect
keys such as `entity_id`, `device_id`, `service`, and target overrides are
rejected in policy and requests. The validator also rejects malformed IDs,
clock skew, excessive TTL, expiry, duplicates, oversized/deep parameters, and
any target outside `ha/...`.
