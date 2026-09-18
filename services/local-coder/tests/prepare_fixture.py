from pathlib import Path
import subprocess,json
root=Path('/mnt/ai-store/local-coder/qualification/coding')
if root.exists() and any(root.iterdir()):
    raise SystemExit('Existing qualification fixture is preserved; archive it explicitly before preparing a new run')
root.mkdir(parents=True,exist_ok=True)
(root/'duration.py').write_text('''import re

def parse_duration(value):
    """Return integer seconds for a duration such as '1h 30m' or '45s'."""
    factors = {"h": 60, "m": 60, "s": 1}
    return sum(int(number) * factors[unit] for number, unit in re.findall(r"(\\d+)([hms])", value))
''')
(root/'test_duration.py').write_text('''import unittest
from duration import parse_duration

class DurationTests(unittest.TestCase):
    def test_hours_minutes(self):
        self.assertEqual(parse_duration("1h 30m"), 5400)
    def test_seconds(self):
        self.assertEqual(parse_duration("45s"), 45)
    def test_reject_trailing_junk(self):
        with self.assertRaises(ValueError): parse_duration("10m garbage")
    def test_reject_empty(self):
        with self.assertRaises(ValueError): parse_duration("")
''')
(root/'README.md').write_text('''# Duration parser
Input is a nonempty string containing integer h, m, s components in descending order, each unit at most once. Leading/trailing whitespace and spaces between components are allowed. Zero is allowed. Reject negative numbers, decimals, repeated or out-of-order units, missing units, and any other text with ValueError. Reject non-string input with TypeError. Run python3 -m unittest -v.\n''')
subprocess.run(['git','init','-q',str(root)],check=True)
subprocess.run(['git','-C',str(root),'add','.'],check=True)
subprocess.run(['git','-C',str(root),'-c','user.name=Local qualification','-c','user.email=local@example.invalid','commit','-qm','Initial fixture'],check=True)
result=subprocess.run(['python3','-m','unittest','-v'],cwd=root,capture_output=True,text=True)
assert result.returncode!=0
Path('/mnt/ai-store/local-coder/evidence/coding-before.txt').write_text(result.stdout+result.stderr)
web=Path('/mnt/ai-store/local-coder/qualification/web');web.mkdir(parents=True,exist_ok=True)
(web/'index.html').write_text('''<!doctype html><html><head><title>Local Coder Browser Test</title></head><body><h1>Local Coder Browser Test</h1><label for="note">Verification note</label><input id="note"><button id="save" onclick="document.getElementById('result').textContent='Saved: '+document.getElementById('note').value">Save note</button><p id="result">No note saved</p></body></html>''')
print('Fixture baseline fails as expected; browser fixture ready')
