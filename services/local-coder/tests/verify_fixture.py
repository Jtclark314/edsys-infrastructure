"""Independent acceptance cases; run after the agent finishes the disposable fixture."""
import importlib.util
import json
from pathlib import Path

root = Path('/mnt/ai-store/local-coder/qualification/coding')
spec = importlib.util.spec_from_file_location('duration_fixture', root / 'duration.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cases = [
    ('1h 30m', 5400), ('2h', 7200), ('1h2m3s', 3723),
    ('0s', 0), (' 90m ', 5400), ('5m 0s', 300),
    ('', ValueError), ('   ', ValueError), ('1h junk', ValueError),
    ('1s 2h', ValueError), ('1h 1h', ValueError), ('-1h', ValueError),
    ('1.5h', ValueError), ('10', ValueError), (None, TypeError), (123, TypeError),
]
for value, expected in cases:
    if isinstance(expected, type):
        try:
            module.parse_duration(value)
        except expected:
            continue
        raise AssertionError(f'{value!r}: expected {expected.__name__}')
    assert module.parse_duration(value) == expected, repr(value)
result = {'passed': True, 'cases': len(cases), 'successes': len(cases)}
evidence = Path('/mnt/ai-store/local-coder/evidence')
evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
(evidence / 'coding-heldout.json').write_text(json.dumps(result) + '\n')
print(json.dumps(result))
