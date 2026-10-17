"""Core-native weather TV checks; all provider/device transports are fake."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from custom_components.edsys_voice_knowledge.bedroom_screen import BedroomScreen
from custom_components.edsys_voice_knowledge.pilot_policy import PilotRefusal
from custom_components.edsys_voice_knowledge.screen_protocol import SequenceGuard, unseal, validate
from custom_components.edsys_voice_knowledge.weather_tv import WeatherTV
import native_screen_checks as screen_checks


class NativeWeatherChecks(screen_checks.NativeScreenChecks):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.denied = set()
        roles = {'daily': 'weather.daily_fixture', 'observed': 'sensor.observed_fixture',
                 'temperature': 'sensor.temperature_fixture', 'humidity': 'sensor.humidity_fixture',
                 'alerts': 'sensor.alerts_fixture'}
        self.weather_config = {'version': 1, 'radar_site': 'KGSP', 'sources': {
            key: {'entity_id': eid, 'device_id': 'fixture', 'config_entry_id': 'fixture',
                  'area_id': None, 'unique_id': key, 'platform': 'fixture', 'name': key}
            for key, eid in roles.items()}}
        self.rows = {binding['entity_id']: SimpleNamespace(disabled_by=None, **{
            key: binding[key] for key in ('device_id', 'config_entry_id', 'unique_id', 'platform')})
            for binding in self.weather_config['sources'].values()}
        self.weather_patches = [
            patch('custom_components.edsys_voice_knowledge.weather_tv.er.async_get',
                  return_value=SimpleNamespace(async_get=lambda eid: self.rows.get(eid, self.registry))),
            patch('custom_components.edsys_voice_knowledge.weather_tv.er.async_get_effective_area_id', return_value=None)]
        for item in self.weather_patches:
            item.start()
        self.states.update({
            roles['daily']: State(roles['daily'], 'sunny', {'temperature_unit': '°F'}),
            roles['observed']: State(roles['observed'], datetime.now(timezone.utc).isoformat()),
            roles['temperature']: State(roles['temperature'], '68', {'unit_of_measurement': '°F'}),
            roles['humidity']: State(roles['humidity'], 'unknown'),
            roles['alerts']: State(roles['alerts'], 'unknown'),
        })
        async def user(_):
            return SimpleNamespace(is_active=True, permissions=SimpleNamespace(
                check_entity=lambda eid, policy: self.authorized and eid not in self.denied))
        self.hass.auth.async_get_user = user
        self.forecasts = [{'datetime': (datetime.now(timezone.utc) + timedelta(days=i)).isoformat(),
                           'temperature': 74 + i, 'templow': 50 + i, 'condition': 'rainy',
                           'precipitation_probability': 40} for i in range(8)]
        self.weather_calls = []
        async def service(domain, service, data, **kwargs):
            self.weather_calls.append((domain, service, data, kwargs))
            if (domain, service) != ('weather', 'get_forecasts'):
                raise AssertionError('Unexpected service')
            return {roles['daily']: {'forecast': self.forecasts}}
        self.hass.services.async_call = service
        self.config['weather'] = self.weather_config
        self.screen = BedroomScreen(self.hass, self.pilot, self.remote, self.config)
        raw = io.BytesIO()
        Image.new('RGB', (40, 40), 'green').save(raw, format='JPEG')
        frame = base64.b64encode(raw.getvalue()).decode('ascii')
        self.radar = {'label': 'NOAA KGSP radar · timestamps on image', 'frames': [frame, frame],
                      'durations_ms': [500, 500], 'source_modified': datetime.now(timezone.utc).isoformat(),
                      'fetched_at': datetime.now(timezone.utc).isoformat(), 'width': 40, 'height': 40}

    async def asyncTearDown(self):
        await self.screen.cancel()
        for item in reversed(self.weather_patches):
            item.stop()
        await super().asyncTearDown()

    def messages(self):
        return [unseal(data['data'], self.screen.key) for method, data in self.sent]

    def radar_fakes(self):
        return (
            patch('custom_components.edsys_voice_knowledge.weather_tv.async_get_clientsession', return_value=object()),
            patch('custom_components.edsys_voice_knowledge.weather_tv.download_radar', AsyncMock(return_value=(b'fixture', datetime.now(timezone.utc)))),
            patch('custom_components.edsys_voice_knowledge.weather_tv.decode_radar', return_value=self.radar))

    async def open_radar(self):
        session, download, decode = self.radar_fakes()
        with session, download, decode:
            return await self.screen.handle(self.call('weather_radar', duration=180))

    async def tick_until(self, predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(.002)

    async def test_forecast_exact_target_context_unknown_data_and_existing_protocol(self):
        call = self.call('weather_forecast', duration=180)
        result = await self.screen.handle(call)
        self.assertTrue(result['rendered'])
        self.assertEqual(result['physical_visibility'], 'owner_confirmation_required')
        domain, service, data, kwargs = self.weather_calls[0]
        self.assertEqual((domain, service, data), ('weather', 'get_forecasts', {'entity_id': 'weather.daily_fixture', 'type': 'daily'}))
        self.assertIs(kwargs['context'], call.context)
        self.assertTrue(kwargs['blocking'] and kwargs['return_response'])
        message = self.messages()[0]
        validate(message)
        self.assertEqual(message['kind'], 'briefing')
        self.assertEqual(message['heading'], 'Weather forecast')
        self.assertIn('Humidity unknown', message['rows'][0])
        self.assertIn('unknown', message['rows'][1])
        self.assertEqual(len(message['rows']), 7)
        self.assertIn('Station observed', message['body'])
        self.assertIn('UTC', message['body'])
        self.assertNotIn('sunny', str(message))  # daily entity state is forecast-derived, not measured
        self.assertEqual(self.calls, [])

    async def test_observation_freshness_uses_source_not_poll_time(self):
        now = datetime.now(timezone.utc)
        for value, expected in [('unknown', 'time unknown'),
                                ((now - timedelta(minutes=16)).isoformat(), 'STALE'),
                                ((now + timedelta(minutes=1)).isoformat(), 'future timestamp')]:
            with self.subTest(value=value):
                self.states['sensor.observed_fixture'] = State('sensor.observed_fixture', value)
                card = await self.screen.weather.forecast(self.call('weather_forecast').context)
                self.assertIn(expected, card['body'])

    async def test_malformed_nested_forecast_and_provider_becoming_unavailable_send_nothing(self):
        for payload in (None, [], 'invalid', {'weather.daily_fixture': None},
                        {'weather.daily_fixture': {'forecast': []}}):
            self.hass.services.async_call = AsyncMock(return_value=payload)
            with self.subTest(payload=payload), self.assertRaises(ServiceValidationError):
                await self.screen.handle(self.call('weather_forecast'))
        async def unavailable(*args, **kwargs):
            self.states['weather.daily_fixture'] = State('weather.daily_fixture', 'unavailable')
            return {'weather.daily_fixture': {'forecast': self.forecasts}}
        self.hass.services.async_call = unavailable
        with self.assertRaises(ServiceValidationError):
            await self.screen.handle(self.call('weather_forecast'))
        self.assertEqual(self.sent, [])

    async def test_pinned_weather_identity_area_and_read_permission_gate_unknown_sources(self):
        binding = self.weather_config['sources']['humidity']
        row = self.rows[binding['entity_id']]
        for field in ('device_id', 'config_entry_id', 'unique_id', 'platform'):
            old = getattr(row, field)
            setattr(row, field, 'changed')
            with self.subTest(field=field), self.assertRaises(ServiceValidationError):
                await self.screen.handle(self.call('weather_forecast'))
            setattr(row, field, old)
        self.denied.add(binding['entity_id'])
        with self.assertRaises(ServiceValidationError):
            await self.screen.handle(self.call('weather_forecast'))
        self.denied.clear()
        with patch('custom_components.edsys_voice_knowledge.weather_tv.er.async_get_effective_area_id', return_value='changed'):
            with self.assertRaises(ServiceValidationError):
                await self.screen.handle(self.call('weather_forecast'))
        self.assertEqual(self.sent, [])

    async def test_provider_or_initial_radar_failure_preserves_prior_owner_without_notify(self):
        await self.open_radar()
        previous, task, count = self.screen.active, self.screen.task, len(self.sent)
        with patch('custom_components.edsys_voice_knowledge.weather_tv.download_radar', AsyncMock(side_effect=ValueError('Weather radar is unavailable.'))), \
             patch('custom_components.edsys_voice_knowledge.weather_tv.async_get_clientsession', return_value=object()):
            with self.assertRaises(ServiceValidationError):
                await self.screen.handle(self.call('weather_radar'))
        self.assertEqual((self.screen.active, self.screen.task, len(self.sent)), (previous, task, count))
        self.hass.services.async_call = AsyncMock(side_effect=HomeAssistantError('fixture provider failure'))
        with self.assertRaises(ServiceValidationError):
            await self.screen.handle(self.call('weather_forecast'))
        self.assertEqual((self.screen.active, self.screen.task, len(self.sent)), (previous, task, count))

    async def test_radar_frames_are_bounded_ordered_fixed_expiry_and_cancelled_on_replacement(self):
        with patch('custom_components.edsys_voice_knowledge.weather_tv.FRAME_SECONDS', .005):
            await self.open_radar()
            await self.tick_until(lambda: len(self.sent) >= 3)
            task = self.screen.task
            await self.screen.handle(self.call('clock'))
            await asyncio.sleep(.02)
        frames = [message for message in self.messages() if message['command'] == 'camera_frame']
        self.assertGreaterEqual(len(frames), 3)
        self.assertEqual([message['sequence'] for message in frames], list(range(len(frames))))
        self.assertEqual(len({message['expires_at'] for message in frames}), 1)
        self.assertTrue(all(message['mode'] == 'large' and 'url' not in message for message in frames))
        self.assertTrue(task.done())
        self.assertEqual(self.messages()[-1]['kind'], 'clock')
        self.assertEqual(self.calls, [])

    async def test_cancel_during_download_or_executor_never_sends_late_frame(self):
        for phase in ('download', 'executor'):
            entered, release = asyncio.Event(), asyncio.Event()
            async def pending_download(*args):
                entered.set()
                await release.wait()
                return b'fixture', datetime.now(timezone.utc)
            async def pending_executor(*args):
                entered.set()
                await release.wait()
                return self.radar
            session, download, decode = self.radar_fakes()
            with session, download, decode:
                target = 'custom_components.edsys_voice_knowledge.weather_tv.download_radar' if phase == 'download' else None
                if target:
                    blocker = patch(target, side_effect=pending_download)
                else:
                    blocker = patch.object(self.hass, 'async_add_executor_job', side_effect=pending_executor)
                with blocker:
                    operation = asyncio.create_task(self.screen.handle(self.call('weather_radar')))
                    await asyncio.wait_for(entered.wait(), 1)
                    await self.screen.cancel()  # The same path used by config-entry unload.
                    release.set()
                    with self.assertRaises(ServiceValidationError):
                        await operation
            self.assertEqual(self.sent, [])
            self.assertIsNone(self.screen.task)

    async def test_radar_permission_or_input_change_stops_frames_and_clears_only_own_view(self):
        for scenario in ('permission', 'input'):
            self.denied.clear()
            self.states['media_player.tv_fixture'] = State('media_player.tv_fixture', 'on', {'app_id': 'com.webos.app.hdmi1'})
            with patch('custom_components.edsys_voice_knowledge.weather_tv.FRAME_SECONDS', .005):
                await self.open_radar()
                task = self.screen.task
                before = len(self.sent)
                key = self.render['request_id']
                if scenario == 'permission':
                    self.denied.add('sensor.humidity_fixture')
                else:
                    self.states['media_player.tv_fixture'] = State('media_player.tv_fixture', 'on', {'app_id': 'netflix'})
                await self.tick_until(lambda: self.screen.task is None)
            messages = self.messages()[before:]
            self.assertEqual([message['command'] for message in messages], ['clear'])
            self.assertEqual(messages[0]['request_id'], key)
            self.assertTrue(task.done())

    async def test_dismiss_external_owner_and_uncertain_send_never_replay_or_clear_new_view(self):
        with patch('custom_components.edsys_voice_knowledge.weather_tv.FRAME_SECONDS', .005):
            for visible, replacement in [(False, False), (True, True)]:
                await self.open_radar()
                before = len(self.sent)
                self.render['visible'] = visible
                if replacement:
                    self.render['request_id'] = 'external-owner'
                await self.tick_until(lambda: self.screen.task is None)
                self.assertEqual(len(self.sent), before)
            await self.open_radar()
            before = len(self.sent)
            self.fail_transport = True
            await self.tick_until(lambda: self.screen.task is None)
            self.assertEqual(len(self.sent), before + 1)

    def test_same_key_cleanup_is_rejected_after_pi_owner_replacement(self):
        guard = SequenceGuard()
        first = self.screen.envelope('00000000-0000-0000-0000-000000000001', 'camera_frame', __import__('time').time() + 30, label='Radar', mode='large', jpeg=self.radar['frames'][0])
        self.assertTrue(guard.accept(first))
        other = self.screen.envelope('00000000-0000-0000-0000-000000000002', 'show', __import__('time').time() + 30, kind='clock', heading='', body='', rows=[], timezone='UTC')
        self.assertTrue(guard.accept(other))
        cleanup = self.screen.envelope(first['request_id'], 'clear', __import__('time').time() + 10, sequence=1)
        self.assertFalse(guard.accept(cleanup))
        self.assertEqual(guard.current, other['request_id'])

    def test_weather_config_rejects_scope_expansion_and_invalid_types(self):
        for config in ({'version': True, 'sources': self.weather_config['sources'], 'radar_site': 'KGSP'},
                       {**self.weather_config, 'url': 'https://example.invalid'},
                       {**self.weather_config, 'radar_site': 'OTHER'},
                       {**self.weather_config, 'sources': {}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                WeatherTV(self.screen, config)


if __name__ == '__main__':
    unittest.main()
