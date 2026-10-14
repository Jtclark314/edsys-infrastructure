"""Offline adapter over the HA-owned reference receiver. No transport or I/O.

The receiver is injected by the qualification tool. This module does not own or
duplicate the shared wire schema. No event can execute a household/player action.
"""
from dataclasses import replace
from datetime import datetime
from model import View


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def clip(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit-1]+'…'


def fact_rows(sections):
    rows = []
    for section in sections:
        if section['empty']:
            rows.append(section['label']+' · No entries')
        elif not section['items']:
            rows.append(section['label']+' · '+section['quality'])
        for fact in section['items']:
            detail = fact['value'] if fact['value'] is not None else fact['quality']
            if fact['quality'] != 'known' and fact['value'] is not None:
                detail = fact['quality']+' · '+detail
            rows.append(fact['label']+' · '+detail)
    return rows


def event_view(event, now):
    """Convert a validated event to a bounded, non-executable view."""
    typ, p = event['type'], event['payload']
    heading, body, rows, kind, state = 'Bedroom companion', '', [], 'butler', 'idle'
    if typ == 'voice_state':
        state = p['state']
        heading = {'listening':'At your service.', 'processing':'One moment.',
                   'responding':'Your reply is ready.', 'failed':'I couldn’t complete that.',
                   'unavailable':'Voice is unavailable.', 'clarification':'A little clarification.',
                   'cancelled':'Cancelled.', 'complete':'Your request has finished.'}.get(state, 'Ready when you are.')
        body = 'Waiting for confirmed endpoint audio.' if p['audio_ref'] else ''
    elif typ == 'caption':
        heading, body = ('You said' if p['speaker']=='user' else 'Your reply'), p['text']
    elif typ == 'result':
        kind, heading, body = 'result', p['outcome'].capitalize(), p['message']
        rows = ['Target · '+p['target_ref'], 'Action · '+p['action'], 'Evidence · '+p['evidence']]
        state = {'verified':'complete', 'submitted':'processing', 'cancelled':'cancelled'}.get(p['outcome'], 'failed' if p['outcome']=='failed' else 'unavailable')
    elif typ == 'choice':
        kind, state, heading = 'result', 'clarification', p['prompt']
        body = 'Selection must return through HA permission checks.'
        rows = [c['label'] for c in p['choices']]
    elif typ == 'camera':
        kind, state, heading = 'camera', 'unavailable', 'Camera · '+p['camera_ref']
        body = 'Live camera display is not qualified.' if p['mode']!='snapshot' else 'Snapshot adapter is not connected.'
        rows = ['Requested mode · '+p['mode'], 'Reported source quality · '+p['quality']]
    elif typ in ('briefing', 'household_status', 'state_snapshot'):
        kind = 'briefing' if typ=='briefing' else 'household'
        heading = ('Good morning.' if p.get('period')=='morning' else 'Before you turn in.') if typ=='briefing' else 'Household overview'
        rows = fact_rows(p['sections'])
        body = 'Current source states; missing data remains explicit.'
    elif typ == 'answer':
        kind, heading, body = 'answer', p['title'], '\n\n'.join(p['blocks'])
        rows = ['Source · '+s['title'] for s in p['sources']]
        rows += [' | '.join(row) for row in p['table']]
        if p['assets']:
            rows.append('Approved image adapter is not connected.')
    elif typ == 'notification':
        kind, heading, body = 'notification', p['title'], p['body']
        rows = [p['priority'].capitalize()+' · '+p['category']]
    elif typ == 'media':
        kind, heading, body = 'media', p['title'] or 'Media companion', p['state'].capitalize()
        rows = ['Player · '+p['player_ref'], 'No playback command is issued by this view']
    elif typ == 'ambient':
        kind, heading = 'ambient', 'Make yourself comfortable.'
        body = 'Ambient mode · '+p['mode']
        if p['asset_ref']:
            rows = ['Approved art adapter is not connected.']
    else:
        raise ValueError('event is not a display view')
    if len(rows)>8:
        rows = rows[:7]+['Additional rows are available in HA.']
    return View.from_dict(dict(key=event['event_id'], kind=kind, heading=clip(heading,100),
                               body=clip(body,1200), items=[clip(r,160) for r in rows],
                               state=state, audio_playing=False,
                               ttl_seconds=(stamp(event['expires_at'])-now).total_seconds()))


class RendererSession:
    """In-memory desired presentation. Disconnect discards all transient state."""
    def __init__(self, receiver):
        self.receiver = receiver
        self.clear()

    def clear(self):
        self.view = self.event = self.expires = None
        self.voice_event = self.voice_expires = None

    def connect(self, connection_id, *, now):
        self.clear()
        self.receiver.connect(connection_id, now=now)

    def disconnect(self):
        self.clear()
        self.receiver.disconnect()

    def accept(self, event, *, now, display_visible=True):
        self.receiver.receive(event, now=now)
        self.tick(now=now)
        typ, p = event['type'], event['payload']
        if self.event and self.event['run_id'] != event['run_id'] and typ not in ('dismiss', 'cancel'):
            self.clear()
        if typ == 'dismiss':
            if self.event and p['target_event_id']==self.event['event_id']:
                self.clear()
        elif typ == 'cancel':
            if self.event and p['target_run_id']==self.event['run_id']:
                self.clear()
        elif not display_visible:
            # Never wake the TV, change input or queue transient alerts here.
            self.clear()
        else:
            if typ == 'state_snapshot':
                self.clear()
            previous = self.view
            self.view = event_view(event, now)
            if typ == 'voice_state':
                self.voice_event, self.voice_expires = event, stamp(event['expires_at'])
            elif typ == 'caption' and p['speaker']=='assistant' and self.voice_event:
                self.view = replace(self.view, state=self.voice_event['payload']['state'],
                                    audio_playing=bool(previous and previous.audio_playing))
            self.event = event
            self.expires = stamp(event['expires_at'])
        return self.view

    def local_audio(self, event_id, audio_ref, *, playing, now):
        """Only an eventual trusted local playback binding may call this method."""
        self.tick(now=now)
        e = self.voice_event
        if (not isinstance(playing, bool) or not e or e['type']!='voice_state'
                or e['event_id']!=event_id or e['payload']['audio_ref'] is None
                or e['payload']['audio_ref']!=audio_ref or self.view.state!='responding'):
            return False
        self.view = replace(self.view, audio_playing=playing)
        return True

    def tick(self, *, now):
        if self.expires and self.expires <= now:
            self.clear()
        elif self.voice_expires and self.voice_expires <= now:
            self.voice_event = self.voice_expires = None
            if self.view:
                self.view = replace(self.view, audio_playing=False)
        return self.view
