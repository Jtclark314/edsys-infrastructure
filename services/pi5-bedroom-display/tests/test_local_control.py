import sys
from pathlib import Path
import unittest
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_control import validate_request


class LocalControlTests(unittest.TestCase):
    def test_only_declared_operations_and_fields(self):
        base={'request_id':str(uuid4())}
        for extra in [{'op':'shell','command':'echo'}, {'op':'key','key':'Powerdown'},
                      {'op':'scene','scene':'../../private'}, {'op':'play_test','url':'file:///private'},
                      {'op':'status','unexpected':True}, {'op':'status','request_id':'invalid'}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                validate_request(base|extra,{'listening':True})
        validate_request(base|{'op':'scene','scene':'listening'},{'listening':True})
        validate_request(base|{'op':'key','key':'back'}, {})
        validate_request(base|{'op':'scene','scene':'listening','duration_seconds':60},{'listening':True})
        for value in [True,0,91,'60']:
            with self.subTest(duration=value),self.assertRaises(ValueError):
                validate_request(base|{'op':'scene','scene':'listening','duration_seconds':value},{'listening':True})


if __name__=='__main__':unittest.main()
