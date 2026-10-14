"""Bounded native HA pilot and Nimo presentation; no generic service tool."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import json
import re
from pathlib import Path
import time

import probatio
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, intent, llm
from homeassistant.util.ulid import ulid_now

from . import DOMAIN
from .pilot_policy import PilotRefusal, check_control, choose_camera


@dataclass
class Request:
    text: str
    context: object
    device_id: str | None
    presentation: dict | None = None
    control_attempted: bool = False
    outcome: dict | None = None


CURRENT_REQUEST: ContextVar[Request | None] = ContextVar("edsys_pilot_request", default=None)


class Pilot:
    def __init__(self, hass, config, nimo_device_id):
        self.hass, self.config, self.nimo_device_id = hass, config, nimo_device_id
        self.lock = asyncio.Lock()
        self.seen = {}
        self.presentations = {}

    async def state(self, key, context, *, control=False):
        binding = self.config["targets"].get(key) or self.config["cameras"].get(key)
        if binding is None:
            raise PilotRefusal("That device is outside the reviewed pilot.")
        entity_id = binding["entity_id"]
        registry = er.async_get(self.hass).async_get(entity_id)
        if registry is None or registry.disabled_by or registry.device_id != binding["device_id"] or registry.config_entry_id != binding["config_entry_id"]:
            raise PilotRefusal("The device mapping changed. It needs review before control.")
        if "area_id" in binding and er.async_get_effective_area_id(self.hass, registry) != binding["area_id"]:
            raise PilotRefusal("The device's room assignment changed. It needs review before control.")
        user_id = getattr(context, "user_id", None)
        user = await self.hass.auth.async_get_user(user_id) if user_id else None
        if user is None or not user.is_active or not user.permissions.check_entity(entity_id, POLICY_CONTROL if control else POLICY_READ):
            raise PilotRefusal("Your Home Assistant account cannot access that device.")
        if control:
            from homeassistant.components.homeassistant.exposed_entities import async_should_expose
            if not async_should_expose(self.hass, "conversation", entity_id):
                raise PilotRefusal("That device is not exposed to Assist.")
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            raise PilotRefusal("That device is currently unavailable in Home Assistant.")
        return state

    async def lookup(self, context):
        result = []
        for key, binding in {**self.config["targets"], **self.config["cameras"]}.items():
            try:
                state = await self.state(key, context)
                status = state.state
            except PilotRefusal as error:
                status = str(error)
            result.append({"target": key, "name": binding["name"], "room": binding.get("room"), "status": status,
                           "aliases": binding.get("aliases", [])})
        return {"scope": "Reviewed Gadget Lab main light, Master Bedroom TV/Plex and native camera viewing on Nimo",
                "devices": result, "desk_downlights_included": False,
                "ambiguous_bedroom": "Ask Master or Secondary; never assume", "camera_analysis": "disabled"}

    async def recognized_control(self, request, recognized):
        """Plan native pilot targets before execution, then use the same validator."""
        intent_name = recognized.get("intent", {}).get("name")
        operations = {"HassTurnOn": "on", "HassTurnOff": "off", "HassMediaPlayerMute": "mute",
                      "HassMediaPlayerUnmute": "unmute", "HassMediaPause": "pause", "HassMediaUnpause": "resume",
                      "HassMediaPlay": "resume", "HassMediaStop": "stop", "HassSetVolume": "set_volume",
                      "HassMediaPlayerSetVolume": "set_volume"}
        operation = operations.get(intent_name)
        targets = [eid for eid, data in recognized.get("targets", {}).items() if data.get("matched")]
        by_entity = {binding["entity_id"]: key for key, binding in self.config["targets"].items()}
        reviewed = [by_entity[eid] for eid in targets if eid in by_entity]
        if not reviewed:
            return None
        if len(targets) != 1:
            raise PilotRefusal("Please name one pilot device at a time.")
        if operation is None:
            raise PilotRefusal("That action is outside the reviewed pilot. Please ask for a supported action.")
        target = reviewed[0]
        if target == "master_bedroom_tv" and operation in ("pause", "resume", "stop"):
            target = "master_bedroom_plex"
        value = None
        if operation == "set_volume":
            details = recognized.get("details", {})
            value = details.get("volume_level", details.get("volume", {})).get("value")
            if not isinstance(value, (int, float)):
                raise PilotRefusal("Please give a clear volume percentage.")
            value /= 100
        return await self.control(request, target, operation, value)

    async def control(self, request, target, operation, value=None):
        check_control(request.text, target, operation, value)
        async with self.lock:
            # Suppress duplicate tool execution inside the same authenticated turn.
            now = time.monotonic()
            self.seen = {key: item for key, item in self.seen.items() if now - item[0] < 120}
            signature = (request.context.id, target, operation, value)
            if signature in self.seen:
                return self.seen[signature][1]
            if len(self.seen) >= 128:
                raise PilotRefusal("The pilot is busy. Please try a fresh command shortly.")
            result = {"status": "outcome unknown", "verified": False, "message": "The command outcome is uncertain; do not repeat it automatically."}
            self.seen[signature] = (now, result)
            state = await self.state(target, request.context, control=True)
            features = state.attributes.get("supported_features", 0)
            from homeassistant.components.media_player import MediaPlayerEntityFeature as F
            feature = {"on": F.TURN_ON, "off": F.TURN_OFF, "mute": F.VOLUME_MUTE, "unmute": F.VOLUME_MUTE,
                       "set_volume": F.VOLUME_SET, "pause": F.PAUSE, "resume": F.PLAY, "stop": F.STOP, "open_plex": F.SELECT_SOURCE}
            if target != "gadget_lab_light" and not features & feature[operation]:
                raise PilotRefusal("That action is not currently supported by this device. TV wake-up needs separate commissioning.")
            if target == "master_bedroom_tv" and state.state == "off" and operation not in ("on", "off", "open_plex"):
                raise PilotRefusal("The Master Bedroom TV is off. Ask to turn it on or open Plex first.")
            if operation == "open_plex":
                if state.state == "off":
                    if not features & F.TURN_ON:
                        raise PilotRefusal("The TV is off and its wake-up action is not available.")
                    request.control_attempted = True
                    request.outcome = result
                    try:
                        async with asyncio.timeout(28):
                            await self.hass.services.async_call("media_player", "turn_on", {"entity_id": state.entity_id}, blocking=True, context=request.context)
                            while True:
                                current = self.hass.states.get(state.entity_id)
                                if current and current.state not in ("off", "unknown", "unavailable") and current.attributes.get("source_list"):
                                    state = await self.state(target, request.context, control=True)
                                    break
                                await asyncio.sleep(0.3)
                    except (TimeoutError, HomeAssistantError):
                        return result
            if target.endswith("plex") and state.state not in ("playing", "paused", "buffering"):
                raise PilotRefusal("There is no active Master Bedroom Plex session to control.")
            if target.endswith("plex") and "plex" not in request.text.casefold():
                tv = await self.state("master_bedroom_tv", request.context, control=True)
                if not any(str(tv.attributes.get(key, "")).casefold() == "plex" for key in ("source", "app_name")):
                    raise PilotRefusal("The TV is not reporting Plex as its active app. Please name the intended player.")
            data = {"entity_id": state.entity_id}
            if target == "gadget_lab_light":
                domain, service = "light", "turn_" + operation
                expected = lambda s: s.state == operation
            else:
                domain = "media_player"
                service = {"on": "turn_on", "off": "turn_off", "mute": "volume_mute", "unmute": "volume_mute",
                           "set_volume": "volume_set", "pause": "media_pause", "resume": "media_play", "stop": "media_stop",
                           "open_plex": "select_source"}[operation]
                if operation in ("mute", "unmute"):
                    muted = operation == "mute"; data["is_volume_muted"] = muted
                    expected = lambda s: s.attributes.get("is_volume_muted") is muted
                elif operation == "set_volume":
                    data["volume_level"] = value
                    expected = lambda s: isinstance(s.attributes.get("volume_level"), (int, float)) and abs(s.attributes["volume_level"] - value) < 0.011
                elif operation == "open_plex":
                    sources = [s for s in state.attributes.get("source_list", []) if re.search(r"\bplex\b", s, re.I)]
                    if len(sources) != 1:
                        raise PilotRefusal("The TV does not have a unique verified Plex source.")
                    data["source"] = sources[0]
                    expected = lambda s: s.attributes.get("source") == sources[0] or s.attributes.get("app_name") == sources[0]
                else:
                    wanted = {"on": ("on", "idle", "playing", "paused"), "off": ("off",), "pause": ("paused",),
                              "resume": ("playing",), "stop": ("idle", "off")}[operation]
                    expected = lambda s: s.state in wanted
            if not expected(state):
                request.control_attempted = True
                request.outcome = result
                try:
                    async with asyncio.timeout(18):
                        await self.hass.services.async_call(domain, service, data, blocking=True, context=request.context)
                        while True:
                            current = self.hass.states.get(state.entity_id)
                            if current and current.state not in ("unknown", "unavailable") and expected(current):
                                break
                            await asyncio.sleep(0.3)
                except (TimeoutError, HomeAssistantError):
                    return result
            label = self.config["targets"][target]["name"]
            description = {"on": "on", "off": "off", "mute": "muted", "unmute": "unmuted", "pause": "paused",
                           "resume": "playing", "stop": "stopped"}.get(operation)
            message = f"{label} is {description}." if description else (
                "Plex is open on the Master Bedroom TV." if operation == "open_plex" else f"Master Bedroom TV volume is {round(value * 100)} percent.")
            result = {"status": "verified", "verified": True, "target": target, "operation": operation, "message": message}
            request.outcome = result
            self.seen[signature] = (now, result)
            return result

    async def camera(self, request, requested=None):
        key = choose_camera(request.text, self.config["cameras"], requested)
        if request.device_id != self.nimo_device_id:
            raise PilotRefusal("Which screen should show the camera? This pilot supports the Nimo Voice window.")
        state = await self.state(key, request.context)
        now = time.monotonic()
        self.presentations = {key: item for key, item in self.presentations.items() if now - item["created"] < 45}
        if len(self.presentations) >= 32:
            raise PilotRefusal("Camera presentation is busy. Please try shortly.")
        token = ulid_now()
        card = {"version": 1, "entity_id": state.entity_id, "request_id": token}
        self.presentations[token] = {"created": now, "user_id": request.context.user_id, "entity_id": state.entity_id}
        request.presentation = card
        return {"status": "presentation requested", "verified": False, "camera": self.config["cameras"][key]["name"],
                "message": "Opening the native camera view on Nimo. Visibility is confirmed separately by that window."}


def runtime(hass):
    pilot = hass.data.get(DOMAIN, {}).get("pilot")
    if pilot is None:
        raise PilotRefusal("The home-command pilot is not configured.")
    return pilot


class LookupPilot(llm.Tool):
    name = "lookup_home_pilot"
    description = "Read current reviewed pilot mappings and availability. It excludes unrelated devices and credentials."
    integration = DOMAIN
    parameters = probatio.Schema({})
    annotations = llm.ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=False)

    async def async_call(self, hass, tool_input, llm_context):
        try:
            data = await runtime(hass).lookup(llm_context.context)
            return llm.ToolResult(data=data)
        except PilotRefusal as error:
            return llm.ToolResult(data={"message": str(error)}, error=True)


class ControlPilot(llm.Tool):
    name = "control_home_pilot"
    description = "Execute one explicit current voice command only: Gadget Lab MAIN light on/off, Master Bedroom TV power/mute/volume/Plex launch, or its ACTIVE Plex session pause/resume/stop. Bedroom alone is ambiguous. No arbitrary entity or service is accepted."
    integration = DOMAIN
    parameters = probatio.Schema({
        probatio.Required("target"): probatio.In(["gadget_lab_light", "master_bedroom_tv", "master_bedroom_plex"]),
        probatio.Required("operation"): probatio.In(["on", "off", "mute", "unmute", "set_volume", "open_plex", "pause", "resume", "stop"]),
        probatio.Required("value"): probatio.Any(None, probatio.All(probatio.Coerce(float), probatio.Range(min=0, max=1))),
    })
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=True, open_world=False)

    async def async_call(self, hass, tool_input, llm_context):
        request = CURRENT_REQUEST.get()
        if request is None or request.context.id != getattr(llm_context.context, "id", None):
            return llm.ToolResult(data={"message": "No authenticated current voice request."}, error=True)
        try:
            data = await runtime(hass).control(request, **tool_input.tool_args)
            return llm.ToolResult(data=data, error=not data.get("verified"))
        except PilotRefusal as error:
            request.outcome = {"message": str(error), "verified": False}
            return llm.ToolResult(data=request.outcome, error=True)


class ShowPilotCamera(llm.Tool):
    name = "show_camera_on_nimo"
    description = "Show a uniquely named camera in the REQUESTING Nimo Voice window. No image is sent to OpenAI. TV displays are outside the pilot."
    integration = DOMAIN
    parameters = probatio.Schema({probatio.Required("camera"): str})
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=True, open_world=False)

    async def async_call(self, hass, tool_input, llm_context):
        request = CURRENT_REQUEST.get()
        if request is None or request.context.id != getattr(llm_context.context, "id", None):
            return llm.ToolResult(data={"message": "No authenticated current voice request."}, error=True)
        try:
            return llm.ToolResult(data=await runtime(hass).camera(request, tool_input.tool_args["camera"]))
        except PilotRefusal as error:
            request.outcome = {"message": str(error), "verified": False}
            return llm.ToolResult(data=request.outcome, error=True)


class PilotIntent(intent.IntentHandler):
    def __init__(self, kind):
        self.intent_type = kind

    async def async_handle(self, intent_obj):
        request = Request(intent_obj.text_input or "", intent_obj.context, intent_obj.device_id)
        response = intent_obj.create_response()
        try:
            pilot = runtime(intent_obj.hass)
            if self.intent_type == "EdSysPilotCamera":
                data = await pilot.camera(request)
            else:
                data = await pilot.control(request, "master_bedroom_tv", "open_plex")
            response.async_set_speech(data["message"])
            if request.presentation:
                response.async_set_card("Camera view", json.dumps(request.presentation), "edsys_camera")
        except PilotRefusal as error:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, str(error))
        return response


@websocket_api.websocket_command({probatio.Required("type"): "edsys_voice_knowledge/nimo"})
@callback
def websocket_nimo(hass, connection, msg):
    try:
        pilot = runtime(hass)
        connection.send_result(msg["id"], {"device_id": pilot.nimo_device_id})
    except PilotRefusal:
        connection.send_error(msg["id"], "pilot_unavailable", "The voice pilot is not configured.")


@websocket_api.websocket_command({probatio.Required("type"): "edsys_voice_knowledge/presentation_ack",
                                 probatio.Required("request_id"): str,
                                 probatio.Required("status"): probatio.In(["playing", "image_visible", "failed"])})
@callback
def websocket_ack(hass, connection, msg):
    pilot = runtime(hass)
    item = pilot.presentations.get(msg["request_id"])
    if not item or item["user_id"] != connection.user.id or time.monotonic() - item["created"] >= 45:
        connection.send_error(msg["id"], "expired", "Presentation request expired.")
        return
    item["status"] = msg["status"]
    connection.send_result(msg["id"], {"accepted": True})


async def setup_pilot(hass, entry):
    path = Path(hass.config.path(DOMAIN, "pilot.json"))
    if not path.exists():
        return False
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError("Invalid pilot configuration")
    config = await hass.async_add_executor_job(lambda: json.loads(path.read_text()))
    if config.get("version") != 1 or set(config["targets"]) != {"gadget_lab_light", "master_bedroom_tv", "master_bedroom_plex"} or not 1 <= len(config["cameras"]) <= 12:
        raise ValueError("Unexpected pilot scope")
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "nimo_voice")}, name="Nimo Voice", manufacturer="EdSys",
        model="Windows Assist client", entry_type=dr.DeviceEntryType.SERVICE)
    hass.data.setdefault(DOMAIN, {})["pilot"] = Pilot(hass, config, device.id)
    intent.async_register(hass, PilotIntent("EdSysPilotCamera"))
    intent.async_register(hass, PilotIntent("EdSysPilotPlex"))
    websocket_api.async_register_command(hass, websocket_nimo)
    websocket_api.async_register_command(hass, websocket_ack)
    entry.async_on_unload(lambda: hass.data.get(DOMAIN, {}).pop("pilot", None))
    return True
