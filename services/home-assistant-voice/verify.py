#!/usr/bin/env python3
"""Run read-only synthetic voice fixtures and return routing/tool/audio evidence.

Fixture results belong in a private directory, never Git/RAG. This uses native
HA Assist and its protected Core socket, never an exported provider/API token.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shlex
import socket
import subprocess

WORKER = r'''
import asyncio,base64,json,re,sys,time
from urllib.parse import urlsplit
import aiohttp
from supervisor.const import SOCKET_CORE
async def probe(session,pipeline,fixture):
  async with session.ws_connect('http://localhost/api/websocket',receive_timeout=75) as ws:
    assert (await ws.receive_json())['type']=='auth_ok'
    command={'id':1,'type':'assist_pipeline/run','pipeline':pipeline,
      'start_stage':'stt' if fixture.get('pcm') else 'intent','end_stage':'tts',
      'input':{'sample_rate':16000} if fixture.get('pcm') else {'text':fixture['text']}}
    await ws.send_json(command)
    started=time.monotonic();events=[];errors=[];answer='';transcript='';audio_bytes=0;conversation_id=None
    for _ in range(10000):
      message=await ws.receive_json()
      if message.get('type')=='result':
        if not message.get('success'):raise RuntimeError('Native pipeline rejected fixture')
        continue
      event=message.get('event',{});kind=event.get('type');data=event.get('data',{});events.append(kind)
      if kind=='run-start' and fixture.get('pcm'):
        tag=bytes([data['runner_data']['stt_binary_handler_id']]);pcm=base64.b64decode(fixture['pcm'])
        for i in range(0,len(pcm),2048):await ws.send_bytes(tag+pcm[i:i+2048])
        await ws.send_bytes(tag)
      if kind=='stt-end':transcript=data['stt_output']['text']
      if kind=='intent-end':
        output=data['intent_output'];conversation_id=output.get('conversation_id')
        if output.get('response',{}).get('response_type')=='error':errors.append('native-conversation-error')
        speech=output.get('response',{}).get('speech',{}).get('plain',{});answer=speech.get('speech','')
      if kind=='error':errors.append(data.get('code','unknown'))
      if kind=='tts-end':
        path=urlsplit(data['tts_output']['url']).path
        if not path.startswith('/api/tts_proxy/'):raise RuntimeError('Unexpected native speech URL')
        async with session.get('http://localhost'+path) as response:
          if response.status!=200:raise RuntimeError('Reply audio fetch failed')
          audio_bytes=len(await response.read())
      if kind=='run-end':break
    else:raise RuntimeError('Fixture event bound exceeded')
    tools=[];agents=[]
    if conversation_id and not errors:
      await ws.send_json({'id':2,'type':'conversation/chat_log/subscribe','conversation_id':conversation_id})
      for _ in range(20):
        message=await ws.receive_json()
        if message.get('id')!=2:continue
        if message.get('type')=='result' and not message.get('success'):break
        if message.get('type')=='event':
          contents=message.get('event',{}).get('data',{}).get('content',[])
          for item in contents:
            if item.get('role')=='assistant':
              if item.get('agent_id'):agents.append(item['agent_id'])
              tools.extend(call.get('tool_name') for call in item.get('tool_calls',[]) if call.get('tool_name'))
          break
    return {'case':fixture['case'],'transcript':transcript,'answer':answer,
      'word_count':len(re.findall(r'\b\w+\b',answer)),'agent_ids':sorted(set(agents)),
      'tool_names':tools,'events':events,'errors':errors,'reply_audio_bytes':audio_bytes,
      'elapsed_seconds':round(time.monotonic()-started,3)}
async def main(p):
  async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(SOCKET_CORE)),
                                  timeout=aiohttp.ClientTimeout(total=180)) as session:
    results=[]
    for fixture in p['fixtures']:results.append(await probe(session,p['pipeline'],fixture))
    print(json.dumps(results))
asyncio.run(main(json.load(sys.stdin)))
'''


def synthesize(text):
    with socket.create_connection(("127.0.0.1", 10200), timeout=20) as sock:
        stream = sock.makefile("rwb", buffering=0)
        stream.write(json.dumps({"type": "synthesize", "data": {"text": text}}).encode() + b"\n")
        chunks, rate = [], None
        while True:
            event = json.loads(stream.readline())
            if event.get("data_length"):
                event.setdefault("data", {}).update(json.loads(stream.read(event["data_length"])))
            raw = stream.read(event.get("payload_length", 0))
            if event["type"] == "audio-start":
                rate = event["data"]["rate"]
                assert event["data"]["width"] == 2 and event["data"]["channels"] == 1
            if event["type"] == "audio-chunk":
                chunks.append(raw)
            if event["type"] == "audio-stop":
                break
        return subprocess.run([
            "ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1",
            "-i", "pipe:0", "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"
        ], input=b"".join(chunks), capture_output=True, check=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-state", type=Path, required=True)
    parser.add_argument("--production", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--speech-fixture", action="store_true")
    selection.add_argument("--knowledge", action="store_true")
    selection.add_argument("--case", choices=("native-time", "general-chat", "web-search", "edsys-knowledge", "unknown-private", "backend-unavailable"))
    args = parser.parse_args()
    candidate = json.loads((args.private_state / "candidate.json").read_text())
    pipeline = (json.loads((args.private_state / "production-pipeline.json").read_text())
                if args.production else candidate["candidate_pipeline"])
    fixtures = [
        {"case": "native-time", "text": "What time is it?"},
        {"case": "general-chat", "text": "Explain what dew point means in one short sentence."},
        {"case": "web-search", "text": "Search the official Home Assistant website and tell me its latest stable release. Name the source."},
    ]
    if args.knowledge:
        fixtures = [{"case": "edsys-knowledge", "text": "In our EdSys system, what is node3 used for?"}]
    if args.case:
        cases = {fixture["case"]: fixture for fixture in fixtures}
        cases.update({
            "edsys-knowledge": {"case": "edsys-knowledge", "text": "In our EdSys system, what is node3 used for?"},
            "unknown-private": {"case": "unknown-private", "text": "In our EdSys system, what services run on the Zephyr99 host?"},
            "backend-unavailable": {"case": "backend-unavailable", "text": "Explain what dew point means."},
        })
        fixtures = [cases[args.case]]
    if args.speech_fixture:
        fixtures = [{"case": "spoken-general-chat", "text": "Explain what dew point means."}]
        fixtures[0]["pcm"] = base64.b64encode(synthesize(fixtures[0]["text"])).decode()
    command = shlex.join([
        "qm", "guest", "exec", "300", "--pass-stdin", "1", "--timeout", "150", "--",
        "docker", "exec", "-i", "hassio_supervisor", "python3", "-c", WORKER
    ])
    process = subprocess.run([
        "ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "pve-node3", command
    ], input=json.dumps({"pipeline": pipeline["id"], "fixtures": fixtures}),
        capture_output=True, text=True, check=True, timeout=165)
    guest = json.loads(process.stdout)
    if guest.get("exitcode") or guest.get("out-truncated"):
        raise RuntimeError("Private native voice fixture failed or output was truncated")
    results = json.loads(guest["out-data"])
    filename = "speech-results.json" if args.speech_fixture else f"{args.case}-results.json" if args.case else "knowledge-results.json" if args.knowledge else "candidate-results.json"
    path = args.private_state / (("production-" if args.production else "") + filename)
    path.write_text(json.dumps(results)); path.chmod(0o600)
    for result in results:
        print(json.dumps({key: value for key, value in result.items() if key != "events"}))
        if result["case"] == "backend-unavailable":
            assert result["errors"] == ["native-conversation-error"]
            assert "temporarily unavailable" in result["answer"]
            assert result["reply_audio_bytes"] > 1000
            continue
        assert not result["errors"] and result["reply_audio_bytes"] > 1000
        assert result["answer"] and result["word_count"] <= 70
        if result["case"] == "web-search":
            assert "web_search_call" in result["tool_names"]
        if result["case"] == "native-time":
            assert any(a in ("homeassistant", "conversation.home_assistant") for a in result["agent_ids"])
        if result["case"] == "edsys-knowledge":
            assert "search_edsys_knowledge" in result["tool_names"]
        if result["case"] == "unknown-private":
            assert "search_edsys_knowledge" in result["tool_names"]
            assert not any(tool == "web_search_call" for tool in result["tool_names"])


if __name__ == "__main__":
    main()
