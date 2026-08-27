"""MQTT command-contract gate; deliberately not a general rule engine."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import signal
import ssl
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from .ledger import DuplicateCommandError, Ledger
from .validator import Policy, ValidationError, validate_command


REQUEST_PREFIX = "edsys/v1/automation/request/"
ACK_PREFIX = "edsys/v1/automation/ack/"
AVAILABILITY_TOPIC = "edsys/v1/availability/edcore-automation/automation-runtime"
HEALTH_PATH = Path(os.environ.get("AUTOMATION_HEALTH_PATH", "/tmp/automation-runtime.healthy"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "level": record.levelname,
                "event": record.getMessage(),
            },
            separators=(",", ":"),
        )


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
LOGGER = logging.getLogger("automation-runtime")
LOGGER.handlers[:] = [handler]
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _availability(status: str) -> str:
    return json.dumps(
        {
            "schema": "edsys.availability.v1",
            "status": status,
            "source": "automation-runtime",
            "ts": _utc_now().isoformat().replace("+00:00", "Z"),
        },
        separators=(",", ":"),
    )


class CommandGate:
    def __init__(self) -> None:
        self.policy = Policy.load(os.environ.get("AUTOMATION_POLICY_PATH", "/app/config/policy.json"))
        self.ledger = Ledger(os.environ.get("AUTOMATION_STATE_DB", "/var/lib/automation-runtime/seen.sqlite3"))
        self.max_clock_skew = int(os.environ.get("AUTOMATION_MAX_CLOCK_SKEW_SECONDS", "30"))
        if not 0 <= self.max_clock_skew <= 300:
            raise RuntimeError("AUTOMATION_MAX_CLOCK_SKEW_SECONDS must be 0..300")

        self.stop_event = threading.Event()
        self.connected = threading.Event()
        self.messages: queue.Queue[tuple[str, bytes, bool]] = queue.Queue(maxsize=1000)
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="automation-runtime-edcore",
            protocol=mqtt.MQTTv5,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(os.environ.get("MQTT_CA_FILE", "/run/secrets/automation_ca_cert"))
        context.load_cert_chain(
            os.environ.get("MQTT_CERT_FILE", "/run/secrets/mqtt_client_cert"),
            os.environ.get("MQTT_KEY_FILE", "/run/secrets/mqtt_client_key"),
        )
        self.client.tls_set_context(context)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.will_set(AVAILABILITY_TOPIC, _availability("offline"), qos=1, retain=False)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            LOGGER.error("mqtt_connect_rejected")
            return
        # MQTT v5 RAP preserves the publisher's RETAIN flag even for a live
        # subscription, so a retained request is rejected before validation on
        # first delivery as well as after reconnect.
        options = mqtt.SubscribeOptions(qos=1, retainAsPublished=True, retainHandling=0)
        result, _ = client.subscribe(f"{REQUEST_PREFIX}#", options=options)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error("mqtt_subscribe_failed")
            return
        client.publish(AVAILABILITY_TOPIC, _availability("online"), qos=1, retain=False)
        self.connected.set()
        LOGGER.info("mqtt_connected")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del client, userdata, disconnect_flags, reason_code, properties
        self.connected.clear()
        HEALTH_PATH.unlink(missing_ok=True)
        LOGGER.warning("mqtt_disconnected")

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        del client, userdata
        if not message.topic.startswith(REQUEST_PREFIX):
            return
        try:
            self.messages.put_nowait((message.topic, bytes(message.payload), bool(message.retain)))
        except queue.Full:
            LOGGER.error("command_queue_full")

    def _publish_ack(
        self,
        reference: str,
        status: str,
        reason_code: str,
        *,
        correlation_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema": "edsys.command.ack.v1",
            "id": reference if len(reference) == 36 else None,
            "status": status,
            "reason_code": reason_code,
            "source": "automation-runtime",
            "ts": _utc_now().isoformat().replace("+00:00", "Z"),
        }
        if correlation_id:
            payload["correlation_id"] = correlation_id
        info = self.client.publish(
            f"{ACK_PREFIX}{reference}",
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            LOGGER.error("ack_publish_timeout")

    def _handle(self, topic: str, payload: bytes, retained: bool = False) -> None:
        fingerprint = "invalid-" + hashlib.sha256(payload).hexdigest()[:16]
        if retained:
            self._publish_ack(fingerprint, "rejected", "retained_request")
            LOGGER.warning("command_rejected:retained_request")
            return
        if len(payload) > 65536:
            self._publish_ack(fingerprint, "rejected", "envelope_too_large")
            LOGGER.warning("command_rejected:envelope_too_large")
            return
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._publish_ack(fingerprint, "rejected", "invalid_json")
            LOGGER.warning("command_rejected:invalid_json")
            return

        if not topic.startswith(REQUEST_PREFIX) or topic == REQUEST_PREFIX:
            self._publish_ack(fingerprint, "rejected", "invalid_request_topic")
            LOGGER.warning("command_rejected:invalid_request_topic")
            return

        reference = decoded.get("id") if isinstance(decoded, dict) else None
        if not isinstance(reference, str) or len(reference) != 36:
            reference = fingerprint
        correlation = decoded.get("correlation_id") if isinstance(decoded, dict) else None
        try:
            command = validate_command(
                decoded,
                self.policy,
                now=_utc_now(),
                max_clock_skew_seconds=self.max_clock_skew,
            )
            self.ledger.claim(command.command_id, command.expires_at, _utc_now())
        except ValidationError as exc:
            self._publish_ack(reference, "rejected", exc.code, correlation_id=correlation)
            LOGGER.warning(f"command_rejected:{exc.code}")
            return
        except DuplicateCommandError:
            self._publish_ack(command.command_id, "rejected", "duplicate", correlation_id=command.correlation_id)
            LOGGER.warning("command_rejected:duplicate")
            return

        properties = Properties(PacketTypes.PUBLISH)
        remaining = max(1, int((command.expires_at - _utc_now()).total_seconds()))
        properties.MessageExpiryInterval = remaining
        info = self.client.publish(
            command.output_topic,
            json.dumps(command.output_payload(), separators=(",", ":")),
            qos=1,
            retain=False,
            properties=properties,
        )
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            # QoS 1 timeout is an unknown outcome, not proof that the broker
            # rejected the PUBLISH. Preserve the claimed ID so a retry cannot
            # duplicate an actuator command that may already be in flight.
            self._publish_ack(
                command.command_id,
                "rejected",
                "publish_outcome_unknown",
                correlation_id=command.correlation_id,
            )
            LOGGER.error("command_publish_outcome_unknown")
            return
        self.ledger.mark_published(command.command_id)
        self._publish_ack(command.command_id, "accepted", "validated_and_published", correlation_id=command.correlation_id)
        LOGGER.info("command_validated_and_published")

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                item = self.messages.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._handle(*item)
            except Exception:
                LOGGER.exception("command_processing_internal_error")
            finally:
                self.messages.task_done()

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(10):
            if self.connected.is_set():
                HEALTH_PATH.write_text(
                    _utc_now().isoformat().replace("+00:00", "Z") + "\n",
                    encoding="utf-8",
                )
            else:
                HEALTH_PATH.unlink(missing_ok=True)

    def run(self) -> None:
        worker = threading.Thread(target=self._worker, name="command-worker", daemon=True)
        heartbeat = threading.Thread(target=self._heartbeat, name="health-heartbeat", daemon=True)
        worker.start()
        heartbeat.start()
        host = os.environ.get("MQTT_HOST", "mosquitto")
        port = int(os.environ.get("MQTT_PORT", "8883"))
        self.client.connect(host, port, keepalive=60, clean_start=mqtt.MQTT_CLEAN_START_FIRST_ONLY)
        self.client.loop_start()
        LOGGER.info("service_started")
        self.stop_event.wait()
        if self.connected.is_set():
            info = self.client.publish(AVAILABILITY_TOPIC, _availability("offline"), qos=1, retain=False)
            info.wait_for_publish(timeout=2)
        self.client.disconnect()
        self.client.loop_stop()
        worker.join(timeout=30)
        heartbeat.join(timeout=30)
        if worker.is_alive() or heartbeat.is_alive():
            raise RuntimeError("automation runtime worker did not stop cleanly")
        HEALTH_PATH.unlink(missing_ok=True)
        self.ledger.close()
        LOGGER.info("service_stopped")


def main() -> None:
    gate = CommandGate()

    def stop(signum: int, frame: Any) -> None:
        del signum, frame
        gate.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    gate.run()


if __name__ == "__main__":
    main()
