#!/usr/bin/env python3
"""Qualify renderer against an explicit HA-owned, synthetic contract directory."""
import argparse
from copy import deepcopy
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contract_adapter import RendererSession, stamp

FILES = ['presentation.schema.json', 'protocol.py', 'fixtures/context.json',
         'fixtures/presentation-events.json', 'fixtures/endpoint-feedback.json',
         'fixtures/selection.json', 'fixtures/edge-cases.json', 'prepare.py',
         'schema_source.py', 'build_fixtures.py']


def digests(root):
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in FILES}


def qualify(root):
    before = digests(root)
    manifest_bytes=(root/'proposal-manifest.json').read_bytes()
    manifest=json.loads(manifest_bytes)
    if manifest.get('schema_version')!=1 or manifest.get('sha256_by_path')!=before:
        raise RuntimeError('Published artifact manifest does not match the contract files')
    ctx = json.loads((root/'fixtures/context.json').read_text())
    if ctx.get('synthetic') is not True:
        raise ValueError('Only explicitly synthetic fixture sets may be replayed')
    spec = importlib.util.spec_from_file_location('bedroom_reference_protocol', root/'protocol.py')
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    frames = json.loads((root/'fixtures/presentation-events.json').read_text())
    now, connected = stamp(ctx['clock']), stamp(ctx['connected_at'])
    passed = []

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    def session():
        result = RendererSession(protocol.EventReceiver(ctx['endpoint'], ctx['room']))
        result.connect(ctx['connection_id'], now=connected)
        return result

    def rejected(operation):
        try:
            operation()
        except protocol.Rejected:
            return
        raise AssertionError('Expected reference guard rejection')

    s = session()
    for frame in frames:
        view = s.accept(frame, now=now)
        if frame['type']=='camera':
            check(view.state=='unavailable', 'Unimplemented camera incorrectly labeled live')
        if frame['type']=='voice_state' and frame['payload']['audio_ref']:
            check(not view.speaking, 'Generated reply incorrectly asserted audio playback')
    passed.append('all synthetic presentation fixtures consumed through shared receiver')
    late=deepcopy(frames[1]);late.update(sequence=frames[-1]['sequence']+1,event_id=str(uuid4()))
    rejected(lambda:s.accept(late,now=now))
    passed.append('cancelled run rejects a late reply')

    edges=json.loads((root/'fixtures/edge-cases.json').read_text())
    for edge in edges:
        fresh=session()
        fresh.accept(frames[0],now=now)
        fresh.accept(edge,now=now)
    passed.append('all independent outcome/freshness/camera edge fixtures consumed')

    feedback=protocol.FeedbackGuard(ctx['endpoint'],ctx['room'],ctx['connection_id'])
    voice=next(f for f in frames if f['type']=='voice_state' and f['payload']['audio_ref'])
    feedback.register(voice,now=now)
    for ack in json.loads((root/'fixtures/endpoint-feedback.json').read_text()):
        result=feedback.receive(ack,now=now)
        check(result['physical_audibility_verified'] is False,'Software feedback asserted audibility')
    choice=next(f for f in frames if f['type']=='choice')
    selection=json.loads((root/'fixtures/selection.json').read_text())
    feedback=protocol.FeedbackGuard(ctx['endpoint'],ctx['room'],ctx['connection_id'])
    feedback.register(choice,now=now)
    result=feedback.receive(selection,now=now,permitted=True)
    check(result.get('execute_action') is False,'Selection executed an action')
    passed.append('shared audio/selection feedback remains correlated and non-actuating')

    s = session()
    snapshot = frames[0]
    s.accept(snapshot, now=now)
    original = s.view
    rejected(lambda:s.accept(snapshot, now=now))
    bad = deepcopy(frames[1]); bad['room']='room/wrong-target'
    rejected(lambda:s.accept(bad, now=now))
    check(s.view==original, 'Rejected data changed the visible frame')
    bad=deepcopy(frames[1]);bad['expires_at']=ctx['clock']
    rejected(lambda:s.accept(bad, now=now))
    passed.append('duplicate, wrong target and expired event rejection leaves view intact')

    s=session()
    replying=None
    for frame in frames:
        s.accept(frame, now=now)
        if frame['type']=='voice_state' and frame['payload']['audio_ref']:
            replying=frame
            break
    check(replying is not None, 'Reply-ready fixture missing')
    event_id, audio_ref=replying['event_id'],replying['payload']['audio_ref']
    check(not s.local_audio(event_id,'audio/wrong',playing=True,now=now), 'Wrong audio reference accepted')
    check(s.local_audio(event_id,audio_ref,playing=True,now=now) and s.view.speaking, 'Matched local playback did not animate')
    caption=deepcopy(next(f for f in frames if f['type']=='caption'))
    caption.update(event_id=str(uuid4()),sequence=replying['sequence']+1)
    caption['payload'].update(speaker='assistant',text='A synthetic reply caption.')
    s.accept(caption,now=now)
    check(s.view.speaking, 'Caption interrupted the correlated playback animation')
    check(s.local_audio(event_id,audio_ref,playing=False,now=now) and not s.view.speaking, 'Finished audio still animates')
    s.tick(now=stamp(caption['expires_at']))
    check(s.view is None and not s.local_audio(event_id,audio_ref,playing=True,now=now+timedelta(minutes=5)), 'Expired audio presentation survived')
    passed.append('local playback correlation survives captions and clears on finish/expiry')

    s=session()
    s.accept(snapshot,now=now)
    s.disconnect()
    check(s.view is None, 'Disconnect retained a transient view')
    s.connect(str(uuid4()),now=now+timedelta(seconds=2))
    rejected(lambda:s.accept(frames[1],now=now+timedelta(seconds=3)))
    check(s.view is None, 'Reconnect replayed an old view')
    passed.append('disconnect clears view and reconnect rejects previous connection')

    s=session()
    check(s.accept(snapshot,now=now,display_visible=False) is None, 'Hidden display retained snapshot')
    check(s.accept(frames[1],now=now,display_visible=False) is None, 'Hidden display queued a transient')
    passed.append('hidden display does not retain or replay transient presentation')

    if before!=digests(root) or manifest_bytes!=(root/'proposal-manifest.json').read_bytes():
        raise RuntimeError('HA artifacts changed during qualification; rerun after publication')
    return {'candidate_only':True, 'live_deployment':False, 'fixture_events':len(frames),
            'edge_events':len(edges),'artifact_sha256':manifest['artifact_sha256'],
            'checks':passed, 'reference_sha256':before}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--contract-dir', required=True, type=Path)
    print(json.dumps(qualify(p.parse_args().contract_dir.resolve()),indent=2))
