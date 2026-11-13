"""Opt-in, read-only validation against the running pinned OpenCode v1 schema."""
import json
from pathlib import Path
from urllib.request import urlopen

import jsonschema

SOURCE = Path(__file__).resolve().parents[1]
URL = 'https://9950x.taile832fe.ts.net:8444/doc'

if __name__ == '__main__':
    with urlopen(URL, timeout=15) as response:
        document = json.load(response)
    schema = {'$ref': '#/components/schemas/Config', 'components': document['components']}
    config = json.loads((SOURCE / 'opencode.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(config)
    print('PASS: candidate config validates against the running pinned OpenCode schema')
