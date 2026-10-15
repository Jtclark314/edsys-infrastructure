"""Fixed local decryption helper for Kodi's embedded Python compatibility.

No arguments, socket, URL, logging or executable input. Plaintext returns only
over the parent add-on's pipe. The key never leaves its private Pi file.
"""
import json
from pathlib import Path
import sys
from manual_protocol import MAX_WIRE,unseal

if __name__=='__main__':
    try:
        data=sys.stdin.read(MAX_WIRE+1)
        if len(data)>MAX_WIRE:raise ValueError('Oversized envelope')
        key=bytes.fromhex(Path('/var/lib/edsys-voice/bedroom-screen-key').read_text().strip())
        message=unseal(data,key)
        sys.stdout.write(json.dumps(message,separators=(',',':')))
    except Exception:
        sys.exit(1)
