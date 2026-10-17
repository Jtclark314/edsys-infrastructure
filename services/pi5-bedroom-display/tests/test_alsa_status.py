"""Named ALSA status resolution against a disposable proc-style fixture."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'kodi'))
from alsa_status import playback_status_path,read_playback_status


class AlsaStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def card(self,index,card_id,status='closed'):
        card=self.root/f'card{index}';card.mkdir();(card/'id').write_text(card_id+'\n')
        path=card/'pcm0p/sub0/status';path.parent.mkdir(parents=True);path.write_text(status)
        return path

    def test_resolves_name_after_numeric_card_reordering(self):
        self.card(0,'other');path=self.card(3,'vc4hdmi0')
        self.assertEqual(playback_status_path(root=self.root),path)
        (self.root/'card0').rename(self.root/'card1')
        (self.root/'card3').rename(self.root/'card0')
        self.assertEqual(playback_status_path(root=self.root),self.root/'card0/pcm0p/sub0/status')

    def test_absent_or_duplicate_card_is_rejected(self):
        self.card(0,'other')
        with self.assertRaises(ValueError):playback_status_path(root=self.root)
        self.card(1,'vc4hdmi0');self.card(2,'vc4hdmi0')
        with self.assertRaises(ValueError):playback_status_path(root=self.root)

    def test_missing_playback_status_is_rejected(self):
        path=self.card(0,'vc4hdmi0');path.unlink()
        with self.assertRaises(FileNotFoundError):playback_status_path(root=self.root)

    def test_padded_fields_and_closed_sentinel(self):
        path=self.card(2,'vc4hdmi0','state: RUNNING\nowner_pid   : 4242\nhw_ptr      : 24000\n')
        self.assertEqual(read_playback_status(root=self.root),{'state':'RUNNING','owner_pid':'4242','hw_ptr':'24000'})
        path.write_text('closed\n');self.assertEqual(read_playback_status(root=self.root),{'state':'closed'})

    def test_missing_or_duplicate_state_is_rejected(self):
        path=self.card(0,'vc4hdmi0','hw_ptr: 1\n')
        with self.assertRaises(ValueError):read_playback_status(root=self.root)
        path.write_text('state: RUNNING\nstate: closed\n')
        with self.assertRaises(ValueError):read_playback_status(root=self.root)

    def test_identifiers_and_playback_indices_are_bounded(self):
        for card_id in ['', '0', '../card0','vc4hdmi0/other','vc4hdmi0\n']:
            with self.subTest(card_id=card_id),self.assertRaises(ValueError):playback_status_path(card_id=card_id,root=self.root)
        for device in [-1,True,256,'0']:
            with self.subTest(device=device),self.assertRaises(ValueError):playback_status_path(device=device,root=self.root)


if __name__=='__main__':unittest.main()
