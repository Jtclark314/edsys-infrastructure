"""Keep native intents first while the OpenAI backend gets read-only tools.

Core marks any OpenAI API attachment as CONTROL, even a documentation reader.
This adapter advertises no device control and delegates through the public
conversation API. It avoids Core patches and preserves native intent handling.
"""
from __future__ import annotations

import asyncio
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

    async def async_process(self, user_input):
        state = self.hass.states.get(self.backend)
        if state is None or state.state == "unavailable":
            return self._unavailable(user_input)
        try:
            async with asyncio.timeout(45):
                return await conversation.async_converse(
                    hass=self.hass, text=user_input.text, context=user_input.context,
                    conversation_id=user_input.conversation_id, language=user_input.language,
                    device_id=user_input.device_id, satellite_id=user_input.satellite_id,
                    extra_system_prompt=user_input.extra_system_prompt, agent_id=self.backend,
                )
        except TimeoutError:
            return self._unavailable(user_input)

    def _unavailable(self, user_input):
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                                 "General chat is temporarily unavailable. Home commands still use Home Assistant.")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def _async_handle_message(self, user_input, chat_log):
        return await self.async_process(user_input)
