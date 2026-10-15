"""Software rendering acknowledgement for the manually controlled bedroom screen."""
from homeassistant.components.sensor import SensorEntity
from . import DOMAIN


async def async_setup_entry(hass,entry,async_add_entities):
    controller=hass.data.get(DOMAIN,{}).get('bedroom_screen')
    if controller:async_add_entities([BedroomScreenSensor(entry,controller)])


class BedroomScreenSensor(SensorEntity):
    _attr_name='Bedroom Pi Screen'
    _attr_icon='mdi:television-shimmer'
    _attr_should_poll=False

    def __init__(self,entry,controller):
        self.controller=controller;self._attr_unique_id=entry.entry_id+'_bedroom_screen'

    @property
    def available(self):return self.controller.available

    @property
    def native_value(self):return self.controller.status.get('view','idle')

    @property
    def extra_state_attributes(self):
        s=self.controller.status
        return {k:s[k] for k in ['visible','frames','label','mode','frame_at'] if k in s}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.controller.listeners.append(self.async_write_ha_state)
        self.async_on_remove(lambda:self.controller.listeners.remove(self.async_write_ha_state))
