from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/'pi/cec_policy.py'
spec=spec_from_file_location('cec_policy',p)
module=module_from_spec(spec);spec.loader.exec_module(module)


class CecPolicyTests(unittest.TestCase):
    def test_only_native_cec_defaults_change_and_reapplication_is_safe(self):
        source='<peripherals><peripheral bus="cec" name="CEC Adapter" mapTo="cec"><setting key="enabled" type="bool" value="1"/><setting key="activate_source" value="1"/><setting key="other" value="1"/></peripheral><peripheral bus="usb" mapTo="hid"><setting key="enabled" value="1"/></peripheral></peripherals>'
        updated=module.policy(source)
        self.assertIn('key="other" value="1"',updated)
        self.assertIn('bus="usb" mapTo="hid"><setting key="enabled" value="1"',updated)
        self.assertEqual(updated.count('value="0"'),2)
        self.assertEqual(module.policy(updated),updated)
        with self.assertRaises(ValueError):module.policy('<peripherals/>')


if __name__=='__main__':unittest.main()
