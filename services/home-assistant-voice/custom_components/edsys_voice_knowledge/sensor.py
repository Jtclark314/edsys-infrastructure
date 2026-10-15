"""Software rendering acknowledgement for the manually controlled bedroom screen."""
from homeassistant.components.sensor import SensorEntity
from . import DOMAIN


async def async_setup_entry(hass,entry,async_add_entities):
    controller=hass.data.get(DOMAIN,{}).get('bedroom_screen')
    if controller:async_add_entities([BedroomScreenSensor(entry,controller)])
    comfort=hass.data.get(DOMAIN,{}).get('bedroom_comfort')
    if comfort:async_add_entities([BedroomComfortSensor(entry,comfort),BedroomSleepSensor(entry,comfort)])


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
        return {k:s[k] for k in ['visible','frames','label','mode','frame_at','art_index','expires_at'] if k in s}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.controller.listeners.append(self.async_write_ha_state)
        self.async_on_remove(lambda:self.controller.listeners.remove(self.async_write_ha_state))


class BedroomComfortSensor(BedroomScreenSensor):
    _attr_name='Bedroom Pi Night Controls'
    _attr_icon='mdi:subtitles-outline'

    def __init__(self,entry,controller):
        self.controller=controller;self._attr_unique_id=entry.entry_id+'_bedroom_comfort'

    @property
    def native_value(self):return 'Video active' if self.controller.status.get('video_active') else 'No video'

    @property
    def extra_state_attributes(self):return dict(self.controller.status)


class BedroomSleepSensor(BedroomScreenSensor):
    _attr_name='Bedroom Pi Sleep Timer'
    _attr_icon='mdi:timer-outline'

    def __init__(self,entry,controller):
        self.controller=controller;self._attr_unique_id=entry.entry_id+'_bedroom_sleep'

    @property
    def available(self):return True

    @property
    def native_value(self):return self.controller.timer['state']

    @property
    def extra_state_attributes(self):return {k:v for k,v in self.controller.timer.items() if k!='state'}
