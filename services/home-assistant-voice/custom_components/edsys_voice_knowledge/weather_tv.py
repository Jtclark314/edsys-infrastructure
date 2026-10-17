"""Explicit weather cards over the existing authenticated screen transport."""
from __future__ import annotations

import asyncio
from datetime import timezone
import math
import re
import time
from uuid import uuid4

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .pilot_policy import PilotRefusal
from .weather_display import download_radar, decode_radar

SOURCES = {'daily', 'observed', 'temperature', 'humidity', 'alerts'}
BINDING_FIELDS = {'entity_id', 'device_id', 'config_entry_id', 'area_id', 'unique_id', 'platform', 'name'}
FRAME_SECONDS = 2


def _text(value, limit=160):
    return ' '.join(str(value).split())[:limit]


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


class WeatherTV:
    def __init__(self, screen, config):
        self.screen, self.hass = screen, screen.hass
        self.config = None
        if config is None:
            return
        if (not isinstance(config, dict) or set(config) != {'version', 'sources', 'radar_site'}
                or type(config['version']) is not int or config['version'] != 1
                or config['radar_site'] != 'KGSP' or not isinstance(config['sources'], dict)
                or set(config['sources']) != SOURCES):
            raise ValueError('Unexpected reviewed weather scope')
        for key, binding in config['sources'].items():
            if not isinstance(binding, dict) or set(binding) != BINDING_FIELDS:
                raise ValueError('Incomplete reviewed weather source')
            for field, value in binding.items():
                if value is None and field in {'device_id', 'config_entry_id', 'area_id', 'unique_id'}:
                    continue
                if (not isinstance(value, str) or not 1 <= len(value) <= (255 if field == 'unique_id' else 128)
                        or any(ord(c) < 32 for c in value)):
                    raise ValueError('Invalid reviewed weather source')
            domain = 'weather' if key == 'daily' else 'sensor'
            if not re.fullmatch(domain + r'\.[a-z0-9_]+', binding['entity_id']):
                raise ValueError('Unexpected reviewed weather entity')
        self.config = config

    async def state(self, key, context):
        """Same exact identity/read gate as Pilot, allowing unknown display data."""
        if self.config is None:
            raise PilotRefusal('Weather TV sources have not been configured.')
        binding = self.config['sources'][key]
        row = er.async_get(self.hass).async_get(binding['entity_id'])
        if (row is None or row.disabled_by
                or any(getattr(row, field, None) != binding[field]
                       for field in ('device_id', 'config_entry_id', 'unique_id', 'platform'))
                or er.async_get_effective_area_id(self.hass, row) != binding['area_id']):
            raise PilotRefusal('The weather source mapping changed. Review it before use.')
        user_id = getattr(context, 'user_id', None)
        user = await self.hass.auth.async_get_user(user_id) if user_id else None
        if (user is None or not user.is_active
                or not user.permissions.check_entity(binding['entity_id'], POLICY_READ)):
            raise PilotRefusal('Your Home Assistant account cannot read that weather source.')
        return self.hass.states.get(binding['entity_id'])

    async def sources(self, context):
        return {key: await self.state(key, context) for key in sorted(SOURCES)}

    def observation(self, state, now):
        observed = dt_util.parse_datetime(state.state) if state and state.state not in {'unknown', 'unavailable'} else None
        if observed is None or observed.tzinfo is None:
            return 'Station observation time unknown'
        age = (now - observed).total_seconds()
        freshness = ('future timestamp; check source' if age < 0 else
                     'STALE' if age > 15 * 60 else 'fresh')
        utc = observed.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        local = dt_util.as_local(observed).strftime('%b %d %I:%M %p %Z')
        return f'Station observed {utc} / {local} · {freshness}'

    @staticmethod
    def measurement(state, suffix=''):
        value = _number(state.state) if state else None
        if value is None:
            return 'unknown'
        unit = suffix or _text(state.attributes.get('unit_of_measurement') or '', 12)
        return f'{value:g}{unit}'

    async def forecast(self, context):
        states = await self.sources(context)
        daily = states['daily']
        if daily is None or daily.state in {'unknown', 'unavailable'}:
            raise PilotRefusal('The reviewed daily weather forecast is unavailable.')
        async with asyncio.timeout(10):
            result = await self.hass.services.async_call(
                'weather', 'get_forecasts', {'entity_id': daily.entity_id, 'type': 'daily'},
                blocking=True, return_response=True, context=context)
        entity_result = result.get(daily.entity_id) if isinstance(result, dict) else None
        forecast = entity_result.get('forecast') if isinstance(entity_result, dict) else None
        if not isinstance(forecast, list) or not forecast or not all(isinstance(row, dict) for row in forecast[:5]):
            raise PilotRefusal('The reviewed daily weather forecast returned no daily data.')
        # Recheck identity and read permission after the provider await.
        states = await self.sources(context)
        daily = states['daily']
        if daily is None or daily.state in {'unknown', 'unavailable'}:
            raise PilotRefusal('The reviewed daily weather forecast is unavailable.')
        now = dt_util.now()
        alerts = _number(states['alerts'].state) if states['alerts'] else None
        alert_text = f'{int(alerts)} reported' if alerts is not None and alerts >= 0 and alerts.is_integer() else 'unknown'
        rows = [f'Local station · Temperature {self.measurement(states["temperature"])} · Humidity {self.measurement(states["humidity"], "%")}',
                'NWS alert feed · ' + alert_text]
        unit = _text(daily.attributes.get('temperature_unit') or '', 12)
        for day in forecast[:5]:
            stamp = dt_util.parse_datetime(day.get('datetime', '')) if isinstance(day.get('datetime'), str) else None
            label = dt_util.as_local(stamp).strftime('%a %b %d') if stamp and stamp.tzinfo else 'Date unknown'
            high, low = _number(day.get('temperature')), _number(day.get('templow'))
            high = f'{high:g}{unit}' if high is not None else 'unknown'
            low = f'{low:g}{unit}' if low is not None else 'unknown'
            row = f'{label} · High {high} / Low {low} · {_text(day.get("condition") or "condition unknown", 60)}'
            rain = _number(day.get('precipitation_probability'))
            if rain is not None and 0 <= rain <= 100:
                row += f' · Rain {rain:g}%'
            rows.append(_text(row))
        body = 'WU daily forecast · Generated ' + now.strftime('%b %d %I:%M %p %Z') + '\n' + self.observation(states['observed'], now)
        return dict(kind='briefing', heading='Weather forecast', body=body[:1200],
                    rows=[_text(row) for row in rows[:8]], timezone=self.hass.config.time_zone)

    async def prepare(self, action, context):
        if action == 'weather_forecast':
            return await self.forecast(context)
        await self.sources(context)
        raw, modified = await download_radar(async_get_clientsession(self.hass))
        radar = await self.hass.async_add_executor_job(decode_radar, raw, modified)
        await self.sources(context)
        return radar

    def frame(self, key, expires, sequence, radar, index):
        return self.screen.envelope(key, 'camera_frame', expires, sequence,
                                    label=radar['label'], mode='large', jpeg=radar['frames'][index])

    async def open(self, action, context, duration):
        """Called under the screen lock, after its normal Pi/TV authorization."""
        generation = self.screen.generation
        prepared = await self.prepare(action, context)
        # Failed preparation leaves the existing owned view/task intact.
        self.check_generation(generation)
        await self.screen.authorize(context)
        self.check_generation(generation)
        await self.screen.cancel()
        generation += 1  # Only our own cancel may advance this generation.
        self.check_generation(generation)
        key, expires = str(uuid4()), time.time() + duration
        message = (self.screen.envelope(key, 'show', expires, **prepared)
                   if action == 'weather_forecast' else self.frame(key, expires, 0, prepared, 0))
        await self.screen.send(message)
        self.check_generation(generation)
        status = await self.screen.wait_render(key)
        self.check_generation(generation)
        self.screen.active = key
        if action == 'weather_radar':
            self.screen.task = self.hass.async_create_task(
                self.radar_loop(key, expires, prepared, context), 'Bedroom weather radar')
        return {'rendered': True, 'view': status['view'], 'frames': status.get('frames', 0),
                'physical_visibility': 'owner_confirmation_required'}

    def check_generation(self, generation):
        if self.screen.generation != generation:
            raise PilotRefusal('The weather screen request was cancelled.')

    async def radar_loop(self, key, expires, radar, context):
        sequence, index, uncertain = 1, 1, False
        try:
            while self.screen.active == key and time.time() < expires - 1:
                await asyncio.sleep(FRAME_SECONDS)
                async with self.screen.lock:
                    if self.screen.active != key or time.time() >= expires - 1:
                        return
                    await self.screen.authorize(context)
                    await self.sources(context)
                    status = await self.screen.read()
                    if self.screen.active != key or status.get('request_id') != key or not status.get('visible'):
                        return
                    # A fixed expiry and increasing sequence can never renew the lease.
                    uncertain = True
                    await self.screen.send(self.frame(key, expires, sequence, radar, index % len(radar['frames'])))
                    uncertain = False
                    index += 1
                    sequence += 1
        except asyncio.CancelledError:
            raise
        except self.screen.weather_errors:
            self.screen.status = {'view': 'weather_unavailable', 'visible': False}
            self.screen.changed()
        finally:
            if self.screen.active == key:
                async with self.screen.lock:
                    if self.screen.active == key:
                        try:
                            # Never replay an uncertain presentation RPC. Same-key clear
                            # is rejected by Pi SequenceGuard if another owner replaced it.
                            if not uncertain:
                                status = await self.screen.read()
                                if status.get('request_id') == key and status.get('visible'):
                                    await self.screen.send(self.screen.envelope(key, 'clear', time.time() + 10, sequence))
                        except self.screen.weather_errors:
                            pass
                        finally:
                            self.screen.active = None
            if self.screen.task is asyncio.current_task():
                self.screen.task = None
