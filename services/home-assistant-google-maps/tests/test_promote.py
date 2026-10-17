import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from promote import prepare


class PromotionChecks(unittest.TestCase):
    def setUp(self):
        self.native={'views':[{'title':'Map','type':'panel','cards':[{'type':'map','entities':['person.synthetic'],'hours_to_show':2,'auto_fit':True}]}]}
        self.google={'views':[{'title':'Google Maps Trial','path':'trial','type':'panel','cards':[{'type':'custom:edsys-google-maps-trial','entities':['person.synthetic'],'hours_to_show':2,'api_key':'synthetic-only','renderer_url':'https://renderer.example.test/v1/renderer.html'}]}]}

    def test_exact_fallback_and_private_fields_preserved_without_input_mutation(self):
        before=copy.deepcopy((self.native,self.google));result=prepare(self.native,self.google)
        self.assertEqual((self.native,self.google),before)
        self.assertEqual(result['fallback'],self.native)
        self.assertNotIn('path',result['primary']['views'][0])
        self.assertEqual(result['legacy']['views'][0]['path'],'trial')
        card=result['primary']['views'][0]['cards'][0]
        self.assertEqual(card['api_key'],'synthetic-only')
        self.assertEqual(card['default_map_type'],'hybrid')
        self.assertEqual(card['fallback_path'],'/map-fallback')
        self.assertEqual(card['entities'],['person.synthetic'])

    def test_changed_scope_or_existing_primary_rejected(self):
        for mutate in [lambda c:c.update(entities=['person.other']),lambda c:c.update(hours_to_show=24),lambda c:c.update(type='markdown')]:
            changed=copy.deepcopy(self.google);mutate(changed['views'][0]['cards'][0])
            with self.assertRaises(RuntimeError):prepare(self.native,changed)
        with self.assertRaises(RuntimeError):prepare(self.google,self.google)
