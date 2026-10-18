"""Local, initially disarmed OK Nabu worker. No network or audio-file output.

The parent owns consent, foreground/pipeline gates, cooldown and rearming.
Only a fresh JSON arm command may create an input stream. --check never does.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import queue
import sys
import threading
import types

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_LINE = 4096
MAX_EPOCH = 2147483647


def emit(event):
    print(json.dumps(event, separators=(",", ":"), allow_nan=False), flush=True)


def verified_models(directory, manifest_path=None):
    """Check every exact model byte before loading it; never download at runtime."""
    manifest_path = manifest_path or Path(__file__).with_name("models.lock.json")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    result = {}
    for item in manifest["models"]:
        name = item["filename"]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("invalid_model_name")
        path = Path(directory) / name
        if path.is_symlink() or path.stat().st_size != item["size"]:
            raise ValueError("model_size")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("model_digest")
        result[item["role"]] = path
    if set(result) != {"wakeword", "melspectrogram", "embedding"}:
        raise ValueError("model_roles")
    return result


class Predictor:
    def __init__(self, paths):
        # openWakeWord 0.6.0 predates Google's maintained LiteRT package and
        # imports the old module name twice. This alias exists only inside this
        # dedicated worker process. The packages and their files are unmodified.
        import ai_edge_litert.interpreter as interpreter
        compatibility = types.ModuleType("tflite_runtime")
        compatibility.__path__ = []
        compatibility.interpreter = interpreter
        sys.modules["tflite_runtime"] = compatibility
        sys.modules["tflite_runtime.interpreter"] = interpreter
        import numpy as np
        from openwakeword.model import Model
        self.np = np
        self.label = paths["wakeword"].stem
        self.model = Model(
            wakeword_models=[str(paths["wakeword"])],
            inference_framework="tflite",
            melspec_model_path=str(paths["melspectrogram"]),
            embedding_model_path=str(paths["embedding"]),
            enable_speex_noise_suppression=False,
            vad_threshold=0,
            ncpu=1,
        )

    def reset(self):
        self.model.reset()

    def score(self, frame):
        value = float(self.model.predict(self.np.frombuffer(frame, dtype="<i2"))[self.label])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid_prediction")
        return value


class SoundCapture:
    def __init__(self, callback, device):
        import sounddevice as sd
        self.stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1,
            dtype="int16", device=device, callback=callback,
        )

    def start(self):
        self.stream.start()

    def close(self):
        # A failed abort does not prevent a subsequent successful close from
        # proving release. A failed close is fatal; no wake/disarm ack follows.
        try:
            self.stream.abort(ignore_errors=False)
        except Exception:
            pass
        self.stream.close(ignore_errors=False)
        if not self.stream.closed:
            raise RuntimeError("capture_not_closed")


class Worker:
    """State machine with injected predictor/capture for audio-free tests."""
    def __init__(self, predictor, capture_factory, output, threshold=.5, trigger_frames=2):
        self.predictor = predictor
        self.capture_factory = capture_factory
        self.output = output
        self.threshold = threshold
        self.trigger_frames = trigger_frames
        self.epoch = 0
        self.stream = None
        self.armed = False
        self.fatal = False
        self.done = False
        self.hits = 0
        self.frames = queue.Queue(maxsize=8)
        self.overflow = threading.Event()

    def event(self, name, **values):
        self.output({"event": name, "epoch": self.epoch, **values})

    def discard(self):
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        self.hits = 0

    def callback(self, epoch, data, frames, status):
        if not self.armed or epoch != self.epoch:
            return
        if status or frames != FRAME_SAMPLES or len(data) != FRAME_BYTES:
            self.overflow.set()
            return
        try:
            self.frames.put_nowait((epoch, bytes(data)))
        except queue.Full:
            self.overflow.set()

    def release(self):
        self.armed = False
        self.discard()
        if self.stream is not None:
            current = self.stream
            try:
                current.close()
            except Exception:
                self.fatal = True
                self.event("error", code="capture_release")
                return False
            self.stream = None
        self.overflow.clear()
        return True

    def fail(self, code):
        self.fatal = True
        if self.release():
            self.event("error", code=code)

    def command(self, record):
        if self.fatal or self.done:
            return
        if (not isinstance(record, dict) or set(record) != {"command", "epoch"}
                or type(record["epoch"]) is not int
                or not 0 < record["epoch"] <= MAX_EPOCH
                or record["command"] not in ("arm", "disarm", "quit")):
            self.fail("invalid_command")
            return
        if record["epoch"] <= self.epoch:
            self.fail("stale_epoch")
            return
        if not self.release():
            return
        self.epoch = record["epoch"]
        if record["command"] in ("disarm", "quit"):
            self.event("disarmed")
            self.done = record["command"] == "quit"
            return
        try:
            self.predictor.reset()
            epoch = self.epoch
            callback = lambda data, frames, time_info, status: self.callback(epoch, data, frames, status)
            self.stream = self.capture_factory(callback)
            # Allow callbacks from start(), but no inference/wake is processed
            # until start returns successfully and the armed ack is emitted.
            self.armed = True
            self.stream.start()
            self.event("armed")
        except Exception:
            self.fail("capture_start")

    def tick(self):
        if self.fatal or self.done or not self.armed:
            return
        if self.overflow.is_set():
            self.discard()
            self.overflow.clear()
            try:
                self.predictor.reset()
            except Exception:
                self.fail("prediction_reset")
            return
        try:
            epoch, frame = self.frames.get_nowait()
        except queue.Empty:
            return
        if epoch != self.epoch:
            return
        try:
            score = self.predictor.score(frame)
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid_prediction")
        except Exception:
            self.fail("prediction")
            return
        # A callback overflow during inference invalidates the result too.
        if self.overflow.is_set():
            return
        self.hits = self.hits + 1 if score >= self.threshold else 0
        if self.hits >= self.trigger_frames and self.release():
            self.event("wake")


def read_commands(source, commands, eof, invalid):
    """A bounded reader; no unbounded readline or retained input transcript."""
    try:
        while True:
            line = source.readline(MAX_LINE + 1)
            if not line:
                break
            newline = b"\n" if isinstance(line, bytes) else "\n"
            if len(line) > MAX_LINE or not line.endswith(newline):
                invalid.set()
                break
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                invalid.set()
                break
            try:
                commands.put_nowait(record)
            except queue.Full:
                invalid.set()
                break
    finally:
        eof.set()


def serve(worker, source):
    commands = queue.Queue(maxsize=32)
    eof, invalid = threading.Event(), threading.Event()
    reader = threading.Thread(target=read_commands, args=(source, commands, eof, invalid), daemon=True)
    reader.start()
    worker.event("ready")
    try:
        while not worker.fatal and not worker.done:
            if invalid.is_set():
                worker.fail("command_stream")
                break
            # EOF takes precedence over queued arm commands. A dead parent can
            # never arm or leave capture running.
            if eof.is_set():
                worker.release()
                break
            try:
                record = commands.get(timeout=.01)
            except queue.Empty:
                if not eof.is_set() and not invalid.is_set():
                    worker.tick()
                continue
            if eof.is_set() or invalid.is_set():
                continue
            worker.command(record)
    finally:
        worker.release()
    return 1 if worker.fatal else 0


def check(paths):
    predictor = Predictor(paths)
    predictor.reset()
    scores = [predictor.score(bytes(FRAME_BYTES)) for _ in range(50)]
    if max(scores) >= .5:
        raise ValueError("silence_detection")
    # query_devices() enumerates metadata. No InputStream/RawInputStream,
    # check_input_settings, sound playback or microphone recording occurs.
    import sounddevice as sd
    inputs = [{"index": i, "name": item["name"], "hostapi": int(item["hostapi"]),
               "channels": int(item["max_input_channels"]),
               "default_sample_rate": float(item["default_samplerate"])}
              for i, item in enumerate(sd.query_devices()) if item["max_input_channels"] > 0]
    emit({"event": "ready", "epoch": 0, "check": True, "model_count": len(paths),
          "silence_frames": len(scores), "maximum_silence_score": max(scores),
          "input_devices": inputs, "microphone_opened": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=.5)
    parser.add_argument("--trigger-frames", type=int, default=2)
    parser.add_argument("--device", default=None, help="PortAudio input index or exact name; default Windows input if omitted")
    parser.add_argument("--check", action="store_true", help="Verify models, infer synthetic silence, enumerate devices; no capture")
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or not 0 < args.threshold <= 1 or not 1 <= args.trigger_frames <= 10:
        parser.error("invalid threshold or trigger frame count")
    device = int(args.device) if args.device is not None and args.device.isdecimal() else args.device
    try:
        paths = verified_models(args.models_dir)
        if args.check:
            check(paths)
            return 0
        worker = Worker(Predictor(paths), lambda callback: SoundCapture(callback, device), emit,
                        args.threshold, args.trigger_frames)
        # Read the unbuffered OS pipe in the daemon thread. A buffered stdin
        # reader can hold Python's stream lock during process finalization if
        # the parent keeps stdin open after quit/error.
        return serve(worker, sys.stdin.buffer.raw)
    except Exception:
        # Native stderr can contain library diagnostics; do not print arbitrary
        # exception paths, environment data or device/session state to stdout.
        emit({"event": "error", "epoch": 0, "code": "initialization"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
