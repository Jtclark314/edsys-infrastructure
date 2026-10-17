import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build import build
from deploy import candidate


class BuildChecks(unittest.TestCase):
    def test_only_separate_trial_card_and_no_runtime_key(self):
        data=candidate(['person.synthetic','zone.synthetic'],'https://renderer.example.test/v1/renderer.html')
        self.assertEqual(len(data['views']),1)
        card=data['views'][0]['cards'][0]
        self.assertEqual(card['api_key'],'');self.assertEqual(card['hours_to_show'],2)
        self.assertEqual(card['entities'],['person.synthetic','zone.synthetic'])

    def test_invalid_or_ambiguous_targets_refused(self):
        for targets in [[],['light.synthetic'],['person.synthetic','person.synthetic']]:
            with self.subTest(targets=targets),self.assertRaises(ValueError):candidate(targets,'https://renderer.example.test/v1/renderer.html')

    def test_renderer_build_is_exact_and_excludes_other_paid_features(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(build(),(root/'renderer.html').read_text())
        text=build()
        self.assertIn('streetViewControl:false',text)
        self.assertNotIn('Geocoder(',text);self.assertNotIn('StreetViewPanorama(',text)
        self.assertNotIn('PlacesService(',text);self.assertNotIn('DirectionsService(',text)


if __name__=='__main__':unittest.main()
