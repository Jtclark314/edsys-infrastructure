"""Read-only named ALSA playback status; numeric card order is not an identity."""
from pathlib import Path
import re


def playback_status_path(card_id='vc4hdmi0', device=0, subdevice=0, root=Path('/proc/asound')):
    if not isinstance(card_id,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}',card_id):
        raise ValueError('A stable ALSA card ID is required')
    if any(type(n) is not int or not 0<=n<=255 for n in (device,subdevice)):
        raise ValueError('Invalid ALSA playback device')
    cards=[p for p in Path(root).iterdir() if re.fullmatch(r'card[0-9]+',p.name) and p.is_dir()]
    matches=[p for p in cards if (p/'id').read_text().strip()==card_id]
    if len(matches)!=1:raise ValueError('Named ALSA card is absent or ambiguous')
    status=matches[0]/f'pcm{device}p'/f'sub{subdevice}'/'status'
    if not status.is_file():raise FileNotFoundError('Named ALSA playback status is absent')
    return status


def read_playback_status(card_id='vc4hdmi0', device=0, subdevice=0, root=Path('/proc/asound')):
    # Resolve on every observation: hotplug may renumber cards between reads.
    path=playback_status_path(card_id,device,subdevice,root)
    raw=path.read_text().strip()
    if (path.parents[2]/'id').read_text().strip()!=card_id:
        raise ValueError('ALSA card identity changed during observation')
    if raw=='closed':return {'state':'closed'}
    fields={}
    for line in raw.splitlines():
        if ':' not in line:continue
        key,value=(part.strip() for part in line.split(':',1))
        if not key or key in fields:raise ValueError('Invalid ALSA status fields')
        fields[key]=value
    if not fields.get('state'):raise ValueError('ALSA playback state is missing')
    return fields
