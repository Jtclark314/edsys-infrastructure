# Optional local OK Nabu worker

This worker is prepared for Nimo's existing Home Assistant client. It starts
disarmed, opens no microphone until the parent explicitly arms it, and never
starts Assist or sends Home Assistant commands itself. It has no network
listener, speech recognizer, transcript store, audio files, playback or cloud
connection. Audio exists only in bounded in-memory frames while armed.

The parent must enforce the owner's activation choice and all existing voice /
playback testing holds. Installing dependencies, fetching models, running the
tests or running `--check` does not authorize microphone activation.

## Preparation and silent check

Use a dedicated Windows x64 CPython 3.11 or 3.12 virtual environment inside the
existing current-user application ACL. The runtime, models and private device
enumeration receipts stay outside Git and RAG. Do not use a privileged service,
automatic startup task, separate open TCP port or an HA access credential.

```powershell
& $WakePython -m pip install --only-binary=:all: --require-hashes -r "$WakeSource\requirements-windows.lock.txt"
& $WakePython -m pip check
& $WakePython -I "$WakeSource\prepare_models.py" --models-dir $WakeModels
& $WakePython -I "$WakeSource\wake_worker.py" --models-dir $WakeModels --check
```

`$WakePython`, `$WakeSource` and `$WakeModels` are task-specific operator paths,
not new environment variables or global settings. `prepare_models.py` is the
only network-using helper, and only runs when explicitly invoked. Downloads
are size-bounded, hashed and atomically replaced. The worker checks each exact
model's size and SHA-256 again before loading it. Downloaded model files are
deliberately absent from this source directory.

`--check` loads the three pinned models, runs 50 frames of generated silence,
rejects unexpected positive scores, and enumerates PortAudio input metadata.
It never constructs an audio stream, records, plays sound, contacts HA or
invokes Assist. Its sole stdout JSON includes `check: true` and
`microphone_opened: false`. Keep actual device names/private paths in the
private deployment receipt. Native library diagnostics may appear on stderr.

The full hashed closure is in `requirements-windows.lock.txt`; the associated
official PyPI wheel filenames, URLs and hashes are in `wheels.lock.json`.
NumPy 1.26.4 and ml_dtypes 0.5.4 retain compatibility with openWakeWord 0.6.0.
LiteRT's dependency `backports.strenum` is pinned to 1.2.8 because 1.3.1 excludes
Python 3.11 and 3.12. The Windows sounddevice 0.5.3 wheel contains PortAudio.
The ONNX runtime remains installed to satisfy openWakeWord's declared dependency
and eager VAD module import;
this configured detector explicitly runs the official TFLite models through
LiteRT, without conversion or ONNX inference.

## Parent protocol

Run with redirected stdin/stdout/stderr and a parent-controlled process lifetime:

```text
python.exe -I wake_worker.py --models-dir <private-model-directory>
```

Optional arguments are `--threshold 0.5`, `--trigger-frames 2` and `--device`
(a PortAudio index or name; omitted uses the system default input). Defaults
are a starting point, with room/microphone and false-trigger acceptance pending.
No configuration file or command line silently enables capture.

The worker emits `{"event":"ready","epoch":0}` after the model loads. It has
no microphone stream at this point. Each subsequent stdin line must contain
exactly `command` and `epoch`. Epoch is an integer from 1 through 2147483647,
strictly increasing for this worker process. A fresh process may start with
any positive epoch. Booleans, stale/duplicate epochs, invalid JSON, extra
properties, an overlong line or a full control queue fail closed.

| Parent command | Worker event and required meaning |
| --- | --- |
| `{"command":"arm","epoch":1}` | `armed` at epoch 1 only after the input stream starts. |
| `{"command":"disarm","epoch":2}` | `disarmed` at epoch 2 only after the previous stream is confirmed closed. |
| `{"command":"quit","epoch":3}` | `disarmed` at epoch 3 after release, followed by process exit. |

After two consecutive 80 ms frames at or above the threshold, the worker closes
capture before emitting `{"event":"wake","epoch":1}`. That event is one-shot:
the worker stays disarmed until a strictly newer arm command arrives. No audio
frame, confidence trace, transcript or model state is included in events.
The parent must reject events from obsolete workers/epochs and must not invoke
Assist following a close failure or uncertain/missing release acknowledgement.

EOF from the parent closes capture and exits; queued arm commands are dropped
at EOF. The audio queue holds at most eight 80 ms frames. Callback/PortAudio
overflow drops pending audio and resets model context and the threshold streak.
Late callbacks from a previous epoch are rejected. Unrecoverable errors emit
only a generic code and exit; an unconfirmed capture close cannot produce a
wake or disarmed acknowledgement. The parent must enforce a bounded shutdown
timeout and terminate the worker if it cannot release normally.

Keep the worker disarmed throughout native Assist microphone capture, thinking,
reply audio, camera presentation, other dialogs and ambiguous completion states.
The parent owns cooldown and cautious rearming; the worker's short model
debounce alone is not an interaction or reply-audio gate. The Nimo client and
HA remain the existing microphone/UI/conversation/audio authorities after a
validated, released wake event.

## Tests and limits

```text
python -B -m unittest discover -s services/nimo-voice/wake -p "test_*.py" -v
```

The stdlib tests inject fake capture/predictor objects and never import or open
an audio device. They check initial disarm, monotonic epochs, capture release
before acknowledgements/wake, one-shot behavior, bad/late frames, bounded
overflow/reset, invalid commands, parent EOF and `--check`'s no-capture boundary.
A passing silent check does not establish real wake recognition, microphone
quality, speaker audibility, response routing, echo resistance or playback
pause/resume. Those remain live acceptance tasks after the owner releases the
hold. The worker does not add acoustic echo cancellation or Windows Speex noise
suppression. Ask the owner to wait for Assist's listening indication before
speaking the command; this bridge deliberately forwards no wake pre-roll audio.

## Upstream evidence and provenance

- [openWakeWord 0.6.0](https://pypi.org/project/openwakeword/0.6.0/) supplies the
  detector and feature-buffer logic; its [Model source](https://github.com/dscripka/openWakeWord/blob/v0.6.0/openwakeword/model.py)
  and [AudioFeatures source](https://github.com/dscripka/openWakeWord/blob/v0.6.0/openwakeword/utils.py)
  define the selected arguments, 16 kHz signed 16-bit PCM and 1280-sample frames.
- The explicit [Rhasspy OK Nabu model](https://github.com/rhasspy/wyoming-openwakeword/blob/v1.10.0/wyoming_openwakeword/models/ok_nabu_v0.1.tflite)
  is necessary because openWakeWord 0.6.0's built-in model registry does not
  include OK Nabu. Its shared feature assets come from the official
  [openWakeWord v0.5.1 release](https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1).
- [Google LiteRT 2.3.0](https://pypi.org/project/ai-edge-litert/2.3.0/) publishes
  Windows x64 CPython 3.11/3.12 wheels. Google's [migration guide](https://developers.google.com/edge/litert/migration)
  identifies `ai_edge_litert.interpreter` as the maintained interpreter API.
  A small process-local module alias bridges openWakeWord's old
  `tflite_runtime.interpreter` import; no installed package files are patched.
- [sounddevice 0.5.3 installation](https://python-sounddevice.readthedocs.io/en/0.5.3/installation.html)
  documents bundled Windows PortAudio and the raw input stream API.
- Upstream openWakeWord models carry CC BY-NC-SA 4.0 restrictions. Retain
  attribution and model provenance; this preparation is for the private
  household instance. The downloaded model bytes and runtime stay private.

Rollback: disable the optional wake choice in the parent, await release/exit,
and retain ordinary Alt+V. Removing only the optional runtime/model directories
does not require changing HA, its voice pipelines or the existing WebView
authentication profile. Never remove the profile as part of this rollback.
