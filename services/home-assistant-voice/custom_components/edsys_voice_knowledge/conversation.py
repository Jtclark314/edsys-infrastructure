"""Keep native recognition local and verify the reviewed pilot before replying.

Core marks any OpenAI API attachment as CONTROL, even a documentation reader.
The optional pilot advertises CONTROL and owns narrowly scoped writes. Other native intents
and general chat use the public conversation API; no Core source is patched.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from homeassistant.components import conversation
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers import intent

from . import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    backend = entry.data["backend_agent"]
    registry = er.async_get(hass)
    entity = registry.async_get(backend)
    provider = hass.config_entries.async_get_entry(entity.config_entry_id) if entity else None
    if provider is None or provider.domain != "openai_conversation":
        raise ValueError("Backend must be an existing native OpenAI conversation entity")
    async_add_entities([EdSysVoice(entry, backend)])


class EdSysVoice(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    _attr_has_entity_name = True
    _attr_name = "EdSys Voice"
    _attr_supported_features = 0
    _attr_supports_streaming = True

    def __init__(self, entry, backend):
        self.entry = entry
        self.backend = backend
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self):
        return "*"

    @property
    def supported_features(self):
        hass = getattr(self, "hass", None)
        return conversation.ConversationEntityFeature.CONTROL if hass and hass.data.get(DOMAIN, {}).get("pilot") else 0

    @property
    def extra_state_attributes(self):
        state = self.hass.states.get(self.backend)
        # Keep this adapter available so native commands work even when the
        # OpenAI backend is disabled/unavailable. Backend readiness is separate.
        return {"backend_ready": state is not None and state.state != "unavailable"}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)
        self.async_on_remove(async_track_state_change_event(self.hass, [self.backend], self._backend_updated))

    async def async_will_remove_from_hass(self):
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @callback
    def _backend_updated(self, event):
        self.async_write_ha_state()

    async def async_process(self, user_input, chat_log=None):
        from .pilot import CURRENT_REQUEST, Request, PilotRefusal
        request = Request(user_input.text, user_input.context, user_input.device_id)
        pilot = self.hass.data.get(DOMAIN, {}).get("pilot")
        if pilot:
            # The named pilot enters this router before native dispatch. Native
            # recognition stays local; its pilot targets use verified execution.
            native = conversation.async_get_agent(self.hass, conversation.HOME_ASSISTANT_AGENT)
            if native is None or not hasattr(native, "async_debug_recognize"):
                return self._pilot_result(user_input, chat_log, {"verified": False, "message": "Native recognition needs requalification before home control."})
            native_input = replace(user_input, language=self.hass.config.language) if user_input.language in (None, "*") else user_input
            recognized = await native.async_debug_recognize(native_input)
            remote = self.hass.data.get(DOMAIN, {}).get("media_remote")
            delegate_media = False
            if remote:
                try:
                    media_result = await remote.local(request, recognized)
                except PilotRefusal as error:
                    return self._pilot_result(user_input, chat_log, {"verified": False, "message": str(error)})
                if media_result is not None:
                    delegate_media = media_result.get("delegate_media", False)
                    if not delegate_media:
                        return self._pilot_result(user_input, chat_log, media_result)
            if not delegate_media and recognized and recognized.get("match"):
                try:
                    data = await pilot.recognized_control(request, recognized)
                except PilotRefusal as error:
                    data = {"verified": False, "message": str(error)}
                if data is not None:
                    return self._pilot_result(user_input, chat_log, data)
                return await conversation.async_converse(
                    hass=self.hass, text=user_input.text, context=user_input.context,
                    conversation_id=user_input.conversation_id, language=native_input.language,
                    device_id=user_input.device_id, satellite_id=user_input.satellite_id,
                    agent_id=conversation.HOME_ASSISTANT_AGENT,
                )
        state = self.hass.states.get(self.backend)
        if state is None or state.state == "unavailable":
            return self._unavailable(user_input)
        try:
            token = CURRENT_REQUEST.set(request)
            async with asyncio.timeout(45):
                result = await conversation.async_converse(
                    hass=self.hass, text=user_input.text, context=user_input.context,
                    conversation_id=user_input.conversation_id, language=user_input.language,
                    device_id=user_input.device_id, satellite_id=user_input.satellite_id,
                    extra_system_prompt=user_input.extra_system_prompt, agent_id=self.backend,
                )
                # Spoken control outcomes come from observed HA state, never
                # a model's paraphrase of an unverified or failed tool call.
                if request.outcome is not None:
                    return self._pilot_result(user_input, chat_log, request.outcome)
                if request.presentation:
                    import json
                    result.response.async_set_card("Camera view", json.dumps(request.presentation), "edsys_camera")
                return result
        except TimeoutError:
            if "request" in locals() and request.control_attempted:
                response = intent.IntentResponse(language=user_input.language)
                if request.outcome and (request.outcome.get("verified") or request.outcome.get("status") == "submitted"):
                    response.async_set_speech(request.outcome["message"])
                else:
                    response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                                             "That command's outcome is uncertain. Please check the device before trying again.")
                return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
            return self._unavailable(user_input)
        finally:
            if "token" in locals():
                CURRENT_REQUEST.reset(token)

    def _unavailable(self, user_input):
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                                 "General chat is temporarily unavailable. Home commands still use Home Assistant.")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    def _pilot_result(self, user_input, chat_log, data):
        response = intent.IntentResponse(language=user_input.language)
        if data.get("verified") or data.get("status") == "submitted":
            response.async_set_speech(data["message"])
        else:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, data["message"])
        if chat_log:
            chat_log.async_add_assistant_content_without_tools(conversation.AssistantContent(agent_id=self.entity_id, content=data["message"]))
        return conversation.ConversationResult(response=response,
            conversation_id=chat_log.conversation_id if chat_log else user_input.conversation_id)

    async def _async_handle_message(self, user_input, chat_log):
        return await self.async_process(user_input, chat_log)
