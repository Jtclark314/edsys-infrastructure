import json
from pathlib import Path
import sys
import unittest
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model import KINDS, PlaybackLease, PlaybackSnapshot, View, kodi_text


class PresentationTests(unittest.TestCase):
    def test_fixture_coverage_and_speech_timing(self):
        views = [View.from_dict(v) for v in json.loads((ROOT/'views.json').read_text())]
        self.assertEqual({v.kind for v in views}, KINDS)
        self.assertFalse(next(v for v in views if v.key=='reply-pending').speaking)
        self.assertTrue(next(v for v in views if v.key=='speaking').speaking)
        self.assertFalse(replace(views[0], audio_playing=True).speaking)

    def test_rejects_unsafe_internal_data(self):
        good = dict(key='test', kind='answer', heading='Example', body='Text')
        for patch in [dict(heading='a'*101), dict(body='\0'), dict(items=['x']*9),
                      dict(audio_playing='true'), dict(ttl_seconds=float('nan')),
                      dict(ttl_seconds=True), dict(ttl_seconds=-1), dict(script='echo')]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                View.from_dict(good | patch)
        self.assertEqual(kodi_text('[COLOR red]hello[/COLOR]'), '(COLOR red)hello(/COLOR)')


class PlaybackOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.before = PlaybackSnapshot('pi-player', 'session-a', 'movie-a', True, 70, 1)
        self.paused = replace(self.before, playing=False, position=71, revision=2)

    def lease(self):
        lease = PlaybackLease(self.before)
        self.assertTrue(lease.acknowledge_pause(self.paused))
        return lease

    def test_only_own_pause_is_resumable_and_only_once(self):
        lease = self.lease()
        self.assertTrue(lease.take_resume_permission(self.paused))
        self.assertFalse(lease.take_resume_permission(self.paused))

    def test_already_paused_or_unacknowledged_is_never_resumed(self):
        self.assertFalse(PlaybackLease(self.before).take_resume_permission(self.paused))
        lease = PlaybackLease(replace(self.before, playing=False))
        self.assertFalse(lease.acknowledge_pause(self.paused))
        self.assertFalse(lease.take_resume_permission(self.paused))

    def test_manual_changes_and_ambiguous_ack_forfeit_restoration(self):
        lease = self.lease()
        lease.manual_change()
        self.assertFalse(lease.take_resume_permission(self.paused))
        lease = PlaybackLease(self.before)
        self.assertFalse(lease.acknowledge_pause(replace(self.paused, revision=3)))
        self.assertFalse(lease.take_resume_permission(self.paused))

    def test_wrong_session_item_player_seek_or_user_revision_forfeits_resume(self):
        for patch in [dict(player='another-player'), dict(session='session-b'),
                      dict(item='movie-b'), dict(playing=True), dict(position=10), dict(revision=4)]:
            with self.subTest(patch=patch):
                self.assertFalse(self.lease().take_resume_permission(replace(self.paused, **patch)))


if __name__ == '__main__':
    unittest.main()
