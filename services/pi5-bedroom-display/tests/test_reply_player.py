"""Fixed reply supervision with fake proc status/process/time, no audio I/O."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'kodi'))
spec=importlib.util.spec_from_file_location('reply_player_fixture',ROOT/'kodi/reply_player.py')
player=importlib.util.module_from_spec(spec);spec.loader.exec_module(player)


def running(position=0,owner=4242):
    return {'state':'RUNNING','owner_pid':str(owner),'hw_ptr':str(position)}


class ProgressTests(unittest.TestCase):
    def test_started_requires_owned_hardware_advancement_once(self):
        p=player.PlaybackProgress(4242,0)
        self.assertFalse(p.observe(running(),.1));self.assertFalse(p.started)
        self.assertTrue(p.observe(running(240),.2));self.assertTrue(p.started)
        self.assertFalse(p.observe(running(480),.3))

    def test_start_stall_and_poststart_stall_have_separate_deadlines(self):
        p=player.PlaybackProgress(4242,0);p.observe(running(),.1)
        with self.assertRaises(TimeoutError):p.observe(running(),3)
        p=player.PlaybackProgress(4242,0);p.observe(running(),.1);p.observe(running(240),.2)
        with self.assertRaises(TimeoutError):p.observe(running(240),2.2)
        with self.assertRaises(TimeoutError):p.observe(running(480),2.3)

    def test_wrong_owner_reset_and_missing_pointer_are_rejected(self):
        for fields in [running(owner=42),running(-1),{'state':'RUNNING','owner_pid':'4242'}]:
            p=player.PlaybackProgress(4242,0)
            with self.assertRaises((ValueError,KeyError)):p.observe(fields,.1)
        p=player.PlaybackProgress(4242,0);p.observe(running(240),.1)
        with self.assertRaises(ValueError):p.observe(running(0),.2)

    def test_nonrunning_after_start_does_not_count_as_progress(self):
        p=player.PlaybackProgress(4242,0);p.observe(running(),.1);p.observe(running(240),.2)
        self.assertFalse(p.observe({'state':'XRUN'},1))
        with self.assertRaises(TimeoutError):p.observe({'state':'closed'},2.2)


class HelperTests(unittest.TestCase):
    def harness(self,status,finish_at=None,eof=False):
        clock=SimpleNamespace(now=0.)
        child=SimpleNamespace(pid=4242,returncode=None,stderr=io.BytesIO())
        def poll():
            if child.returncode is None and finish_at is not None and clock.now>=finish_at:child.returncode=0
            return child.returncode
        child.poll=poll;child.terminate=lambda:setattr(child,'returncode',-15)
        child.kill=lambda:setattr(child,'returncode',-9);child.wait=lambda **_:child.returncode
        path=Mock();path.__str__=Mock(return_value='/run/edsys-bedroom-display/reply-00000000-0000-0000-0000-000000000000.wav')
        path.is_symlink.return_value=False;path.stat.return_value=SimpleNamespace(st_uid=os.getuid())
        wav=Mock();wav.__enter__=Mock(return_value=wav);wav.__exit__=Mock(return_value=False)
        for name,value in [('getnchannels',1),('getsampwidth',2),('getframerate',24000),('getcomptype','NONE'),('getnframes',24000)]:
            getattr(wav,name).return_value=value
        output=io.StringIO()
        with patch.object(player,'Path',return_value=path),patch.object(player.wave,'open',return_value=wav),\
                patch.object(player.subprocess,'Popen',return_value=child) as popen,\
                patch.object(player,'read_playback_status',side_effect=lambda:status(clock.now)),\
                patch.object(player.time,'monotonic',side_effect=lambda:clock.now),\
                patch.object(player.time,'sleep',side_effect=lambda d:setattr(clock,'now',clock.now+d)),\
                patch.object(player.select,'select',return_value=([0],[],[]) if eof else ([],[],[])),\
                patch.object(player.os,'read',return_value=b''),redirect_stdout(output):
            player.run('fixture')
        return [json.loads(l) for l in output.getvalue().splitlines()],popen,child,clock.now

    def test_continuing_owned_progress_then_clean_exit(self):
        events,popen,child,elapsed=self.harness(lambda t:running(int(t*24000)),finish_at=.15)
        self.assertEqual([e['event'] for e in events],['started','finished'])
        self.assertTrue(events[-1]['ok']);self.assertEqual(child.returncode,0)
        self.assertIn('plughw:CARD=vc4hdmi0,DEV=0',popen.call_args.args[0])

    def test_poststart_stall_terminates_and_never_reports_success(self):
        events,_,child,elapsed=self.harness(lambda t:running(min(int(t*24000),600)))
        self.assertEqual(events[0],{'event':'started'})
        self.assertFalse(events[-1]['ok']);self.assertEqual(events[-1]['reason'],'progress_stalled')
        self.assertLess(elapsed,3);self.assertEqual(child.returncode,-15)

    def test_unplug_after_start_terminates_as_uncertain(self):
        def status(t):
            if t>.1:raise FileNotFoundError('fixture unplug')
            return running(int(t*24000))
        events,_,child,_=self.harness(status)
        self.assertFalse(events[-1]['ok']);self.assertEqual(events[-1]['reason'],'status_unavailable')
        self.assertEqual(child.returncode,-15)

    def test_no_progress_and_parent_eof_never_start(self):
        events,_,child,elapsed=self.harness(lambda _:running())
        self.assertEqual(len(events),1);self.assertEqual(events[-1]['reason'],'start_unconfirmed')
        self.assertLess(elapsed,3.1);self.assertEqual(child.returncode,-15)
        events,_,child,_=self.harness(lambda _:running(),eof=True)
        self.assertEqual(len(events),1);self.assertFalse(events[-1]['ok'])
        self.assertEqual(events[-1]['reason'],'parent_closed');self.assertEqual(child.returncode,-15)

    def test_missing_named_status_fails_before_process_launch(self):
        with patch.object(player,'read_playback_status',side_effect=ValueError('ambiguous')),\
                patch.object(player,'Path') as path,patch.object(player.wave,'open') as wav,\
                patch.object(player.subprocess,'Popen') as popen:
            p=path.return_value;p.__str__.return_value='/run/edsys-bedroom-display/reply-00000000-0000-0000-0000-000000000000.wav';p.is_symlink.return_value=False;p.stat.return_value.st_uid=os.getuid()
            w=wav.return_value.__enter__.return_value
            w.getnchannels.return_value=1;w.getsampwidth.return_value=2;w.getframerate.return_value=24000;w.getcomptype.return_value='NONE';w.getnframes.return_value=24000
            with self.assertRaises(ValueError):player.run('fixture')
            popen.assert_not_called()


if __name__=='__main__':unittest.main()
