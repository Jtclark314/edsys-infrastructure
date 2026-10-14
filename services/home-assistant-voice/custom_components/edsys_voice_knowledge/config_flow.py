"""Configure the read-only knowledge and native-priority voice adapter."""
import probatio
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig
from . import DOMAIN


class EdSysKnowledgeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        self._async_abort_entries_match({})
        if user_input is not None:
            return self.async_create_entry(title="EdSys Voice Knowledge", data=user_input)
        return self.async_show_form(step_id="user", data_schema=probatio.Schema({
            probatio.Required("backend_agent"): EntitySelector(EntitySelectorConfig(domain="conversation", integration="openai_conversation"))
        }))
