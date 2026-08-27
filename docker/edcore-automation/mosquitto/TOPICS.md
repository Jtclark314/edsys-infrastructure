# MQTT Contract

The broker is the one authoritative EdSys event bus. Home Assistant remains
the device/entity authority and final actuation boundary.

## Namespaces

- `edsys/v1/telemetry/<class>/<source>`: selected observations.
- `edsys/v1/state/<authority>/<entity>`: state events, not a replacement for
  the Home Assistant state machine.
- `edsys/v1/availability/<host>/<service>`: birth and Last Will messages.
- `edsys/v1/automation/request/<producer>`: requests presented to the command
  validator.
- `edsys/v1/command/ha/<target>`: validated commands consumed by Home
  Assistant only.
- `edsys/v1/automation/ack/<id>`: accepted, rejected, and final outcome
  acknowledgments.
- `edsys/test/v1/replay/<run-id>/...`: non-production replay traffic.

Retained state and discovery are supported on the LAN-facing mTLS listener
`8883` for Home Assistant and Frigate. Production requests use a separate
Compose-only mTLS listener on `8884`; external identities cannot write the
production command namespace, and only `automation-runtime` can. Node-RED and
the runtime hard-code `retain=false`, the runtime rejects retained requests,
and restart acceptance proves the production command namespace is empty. This
preserves legitimate discovery without trusting topic convention alone.
The broker-resident `command-audit` identity can only read
`edsys/v1/command/ha/#` for that proof; restore tests never use an external
Home Assistant or Frigate private key.

## Availability / Last Will

Every long-running automation client uses QoS 1 and a Last Will on its own
availability topic. Command-path availability is never retained. Home
Assistant/Frigate discovery and birth behavior may use their established
retained topics on port 8883. Payloads use this shape:

```json
{"schema":"edsys.availability.v1","status":"offline","source":"example-client","ts":"2026-01-01T00:00:00Z"}
```

Publish the same envelope with `status=online` immediately after connecting.
Consumers infer stale/unavailable when neither a fresh birth nor heartbeat is
seen inside the documented client-specific interval; they must not depend on a
retained birth message.

## Commands and acknowledgments

Producers publish only to `automation/request`. The Git-managed command gate
requires a canonical UUID, UTC creation and expiry timestamps, bounded TTL,
allowlisted Home Assistant target/action, and an exact per-action scalar
parameter schema with numeric ranges or bounded string enums. Authority
redirect fields such as entity/device/service/target overrides are forbidden. It
rejects expired, future-dated, unauthorized, malformed, or duplicate IDs.

Accepted commands are published with QoS 1 and `retain=false`. The immediate
acknowledgment means only that validation and publication succeeded. Home
Assistant must publish a separate final outcome acknowledgment before an
orchestration flow treats actuation as complete.

## Telemetry envelope

```json
{
  "schema": "edsys.telemetry.v1",
  "ts": "2026-01-01T00:00:00Z",
  "source": "sensor-class",
  "metric": "temperature",
  "value": 21.5,
  "unit": "Cel",
  "quality": "good",
  "tags": {"location_class": "living-space"}
}
```

Do not place credentials, raw audio, transcripts, personal names, exact
presence history, or arbitrary serial bytes on the bus.
