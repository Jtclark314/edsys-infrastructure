"""Native TTS result binding/relay outcomes, with no device or provider calls."""
import asyncio
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
import wave

from custom_components.edsys_voice_knowledge import phil_reply as reply
from custom_components.edsys_voice_knowledge.phil_voice import PhilVoice
import native_phil_checks as phil_checks


def silence(seconds=1):
    b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(bytes(48000*seconds))
    return b.getvalue()


class NativeReplyChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=phil_checks.NativePhilChecks.asyncSetUp
    asyncTearDown=phil_checks.NativePhilChecks.asyncTearDown
    asyncSetUpFixture=phil_checks.NativePhilChecks.asyncSetUpFixture
    asyncTearDownFixture=phil_checks.NativePhilChecks.asyncTearDownFixture

    async def lease(self):
        self.voice=PhilVoice(self.screen,'pipeline-fixture','pi')
        result=await self.voice.begin('fixture','pipeline-fixture')
        self.assertEqual(result['output'],'pi')
        lease=self.voice.active;lease.update(tts_engine='tts.fixture',stream_token='ephemeral-fixture')
        return lease

    async def test_conversion_uses_bounded_pcm_and_rejects_long_or_non_audio(self):
        result=await self.screen.hass.async_add_executor_job(reply.pcm_wave,silence())
        with wave.open(io.BytesIO(result),'rb') as w:self.assertEqual((w.getnchannels(),w.getframerate(),w.getnframes()),(1,24000,24000))
        for data in [b'',b'http://untrusted.invalid',silence(61)]:
            with self.assertRaises((ValueError,__import__('subprocess').SubprocessError)):reply.pcm_wave(data)

    async def test_terminal_outcome_is_private_and_cancel_never_allows_fallback(self):
        lease=await self.lease();self.assertFalse(self.voice.status('other',lease['id'])['active'])
        lease['commit_attempted']=True;await self.voice.finish()
        status=self.voice.status('fixture',lease['id'])
        self.assertEqual(status['audio_state'],'cancelled');self.assertFalse(status['fallback_allowed'])
        self.assertNotIn('stream_token',status)

    async def run_relay(self,fail=None,engine='tts.fixture'):
        lease=await self.lease();commands=[]
        async def parts():yield silence()
        async def send(lease,command,**data):
            commands.append(command)
            if command==fail:raise TimeoutError
            return {'state':'finished' if command=='commit' else 'released','started':True,'resume_result':'resumed'}
        self.voice.replies.send=send
        with patch.object(reply.tts,'async_get_stream',return_value=SimpleNamespace(engine=engine,async_stream_result=parts)):
            await self.voice.replies.relay(lease)
        return self.voice.status('fixture',lease['id']),commands

    async def test_clean_relay_releases_once_only_after_actual_finished(self):
        status,commands=await self.run_relay()
        self.assertEqual(commands,['prepare','chunk','chunk','commit','release'])
        self.assertEqual(status['audio_state'],'finished');self.assertFalse(status['active'])
        self.assertEqual(status['resume_result'],'resumed')

    async def test_precommit_failure_allows_same_audio_fallback_but_uncertainty_never_does(self):
        for failure,allowed,state in [('prepare',True,'failed'),('commit',False,'uncertain'),('release',False,'uncertain')]:
            status,commands=await self.run_relay(failure)
            self.assertEqual(status['fallback_allowed'],allowed);self.assertEqual(status['audio_state'],state)
            self.assertEqual(commands.count('commit'),0 if failure=='prepare' else 1)

    async def test_unexpected_provider_is_rejected_before_pi_commands(self):
        status,commands=await self.run_relay(engine='tts.changed')
        self.assertEqual(commands,[]);self.assertTrue(status['fallback_allowed'])

    async def test_native_connection_delegates_binary_and_subscription_ownership(self):
        lease=await self.lease();connection=SimpleNamespace(subscriptions={},send_event=unittest.mock.Mock(),binary_handler=object())
        wrapped=reply.PipelineConnection(connection,self.voice.replies,lease)
        event=SimpleNamespace(type='run-start',data={'tts_output':{'token':'native-only'}})
        wrapped.send_event(11,event)
        self.assertIs(wrapped.subscriptions,connection.subscriptions);self.assertIs(wrapped.binary_handler,connection.binary_handler)
        self.assertEqual(lease['stream_token'],'native-only');connection.send_event.assert_called_once_with(11,event)

    async def test_native_wrapper_validates_schema_owner_device_and_pipeline_once(self):
        lease=await self.lease();hass=self.screen.hass;hass.data={'edsys_voice_knowledge':{'phil_voice':self.voice}}
        self.screen.pilot.nimo_device_id='registered-client'
        connection=SimpleNamespace(user=SimpleNamespace(id='fixture'),send_error=unittest.mock.Mock())
        message=dict(id=99,type='edsys_voice_knowledge/phil_pipeline',phil_session_id=lease['id'],pipeline='pipeline-fixture',device_id='registered-client',start_stage='stt',end_stage='tts',input={'sample_rate':16000})
        native=AsyncMock()
        with patch.object(reply.native_run,'__wrapped__',native),patch.object(reply,'async_get_pipeline',return_value=SimpleNamespace(tts_engine='tts.fixture')):
            await reply.websocket_pipeline.__wrapped__(hass,connection,{**message,'device_id':'different'})
            self.assertFalse(lease.get('pipeline_bound'));native.assert_not_called()
            await reply.websocket_pipeline.__wrapped__(hass,connection,message)
            native.assert_awaited_once();self.assertTrue(lease['pipeline_bound'])
            forwarded=native.call_args.args[2];self.assertEqual(forwarded['type'],'assist_pipeline/run');self.assertNotIn('phil_session_id',forwarded)
            await reply.websocket_pipeline.__wrapped__(hass,connection,message);native.assert_awaited_once()
