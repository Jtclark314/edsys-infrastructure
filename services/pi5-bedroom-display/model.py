"""Renderer-local views and playback ownership; no HA access or player actions."""
from dataclasses import dataclass
import math

KINDS = frozenset({'butler', 'camera', 'briefing', 'household', 'answer',
                   'notification', 'media', 'ambient', 'result'})
STATES = frozenset({'idle', 'listening', 'processing', 'responding', 'complete',
                    'clarification', 'cancelled', 'unavailable', 'failed'})


def bounded_text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('invalid bounded text')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('control character in text')
    return value


@dataclass(frozen=True)
class View:
    """Internal presentation model, explicitly not the shared wire contract."""
    key: str
    kind: str
    heading: str
    body: str
    items: tuple = ()
    state: str = 'idle'
    audio_playing: bool = False
    ttl_seconds: float = 20

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('view must be an object')
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ValueError('unknown view fields')
        d = dict(data)
        for name, limit in [('key', 64), ('heading', 100), ('body', 1200)]:
            d[name] = bounded_text(d.get(name), limit)
        if d.get('kind') not in KINDS or d.get('state', 'idle') not in STATES:
            raise ValueError('unsupported view or voice state')
        items = d.get('items', [])
        if not isinstance(items, (list, tuple)) or len(items) > 8:
            raise ValueError('too many view items')
        d['items'] = tuple(bounded_text(s, 160) for s in items)
        if not isinstance(d.get('audio_playing', False), bool):
            raise ValueError('invalid audio flag')
        ttl = d.get('ttl_seconds', 20)
        if isinstance(ttl, bool) or not isinstance(ttl, (float, int)) or not math.isfinite(ttl) or not 0 < ttl <= 300:
            raise ValueError('invalid view lifetime')
        return cls(**d)

    @property
    def speaking(self):
        # A responding model stage does not prove that audio is playing.
        return self.state == 'responding' and self.audio_playing


def kodi_text(text):
    """Prevent household/model text from becoming Kodi formatting commands."""
    return text.replace('[', '(').replace(']', ')')


@dataclass(frozen=True)
class PlaybackSnapshot:
    player: str
    session: str
    item: str
    playing: bool
    position: float
    revision: int


@dataclass
class PlaybackLease:
    """Policy prototype only. An eventual adapter owns real acknowledgements."""
    before: PlaybackSnapshot
    paused_by_us: bool = False
    invalidated: bool = False
    consumed: bool = False

    def acknowledge_pause(self, current):
        if self.invalidated or self.consumed or self.paused_by_us or not self.before.playing:
            return False
        if not self._same_session(current) or current.playing:
            self.invalidated = True
            return False
        # The real adapter must prove this is its own pause acknowledgement.
        # Exactly one observed state transition is expected; any other mutation
        # forfeits restoration instead of guessing what the user intended.
        if current.revision != self.before.revision + 1:
            self.invalidated = True
            return False
        self.paused_by_us = True
        self._paused_revision = current.revision
        self._paused_position = current.position
        return True

    def manual_change(self):
        self.invalidated = True

    def _same_session(self, current):
        return (self.before.player, self.before.session, self.before.item) == (current.player, current.session, current.item)

    def take_resume_permission(self, current):
        allowed = (self.paused_by_us and not self.invalidated and not self.consumed
                   and self._same_session(current) and not current.playing
                   and current.revision == self._paused_revision
                   and abs(current.position - self._paused_position) <= 0.25)
        self.consumed = True
        return allowed
