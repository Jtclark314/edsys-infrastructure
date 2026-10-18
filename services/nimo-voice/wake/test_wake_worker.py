"""Synthetic state/protocol tests. Never imports an audio runtime or captures audio."""

import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import wake_worker as wake


class FakePredictor:
    def __init__(self, scores=None):
        self.scores = iter(scores or [])
        self.resets = 0

    def reset(self):
        self.resets += 1

    def score(self, frame):
        return next(self.scores)


class FakeCapture:
    def __init__(self, callback, actions):
        self.callback = callback
        self.actions = actions
        self.closed = False
        self.fail_start = False
        self.fail_close = False

    def start(self):
        self.actions.append("start")
        if self.fail_start:
            raise RuntimeError("fixture")

    def close(self):
        self.actions.append("close")
        if self.fail_close:
            raise RuntimeError("fixture")
        self.closed = True

    def frame(self, value=None, status=False, frames=wake.FRAME_SAMPLES):
        self.callback(value if value is not None else bytes(wake.FRAME_BYTES), frames, None, status)


class WorkerTests(unittest.TestCase):
    def fixture(self, scores=None):
        actions, captures, events = [], [], []

        def factory(callback):
            actions.append("create")
            capture = FakeCapture(callback, actions)
            captures.append(capture)
            return capture

        def output(event):
            actions.append(event["event"])
            events.append(event)

        predictor = FakePredictor(scores)
        worker = wake.Worker(predictor, factory, output)
        return worker, predictor, captures, events, actions

    def test_startup_never_creates_capture(self):
        worker, predictor, captures, events, _ = self.fixture()
        worker.tick()
        self.assertFalse(worker.armed)
        self.assertEqual(captures, [])
        self.assertEqual(predictor.resets, 0)

    def test_arm_resets_and_acknowledges_started_capture(self):
        worker, predictor, captures, events, actions = self.fixture()
        worker.command({"command": "arm", "epoch": 1})
        self.assertEqual(actions, ["create", "start", "armed"])
        self.assertEqual(events, [{"event": "armed", "epoch": 1}])
        self.assertEqual(predictor.resets, 1)
        self.assertTrue(worker.armed)

    def test_two_frame_wake_releases_before_event_and_is_one_shot(self):
        worker, _, captures, events, actions = self.fixture([.8, .9, .9])
        worker.command({"command": "arm", "epoch": 1})
        for _ in range(2):
            captures[0].frame()
            worker.tick()
        self.assertEqual(actions[-2:], ["close", "wake"])
        self.assertTrue(captures[0].closed)
        self.assertFalse(worker.armed)
        captures[0].frame()
        worker.tick()
        self.assertEqual([e["event"] for e in events], ["armed", "wake"])

    def test_threshold_hits_must_be_consecutive(self):
        worker, _, captures, events, _ = self.fixture([.9, .1, .9])
        worker.command({"command": "arm", "epoch": 1})
        for _ in range(3):
            captures[0].frame()
            worker.tick()
        self.assertEqual([e["event"] for e in events], ["armed"])

    def test_fresh_epoch_can_rearm_after_one_shot(self):
        worker, _, captures, events, _ = self.fixture([.9, .9])
        worker.command({"command": "arm", "epoch": 1})
        for _ in range(2):
            captures[0].frame()
            worker.tick()
        worker.command({"command": "arm", "epoch": 2})
        self.assertTrue(worker.armed)
        self.assertEqual(len(captures), 2)
        self.assertEqual(events[-1], {"event": "armed", "epoch": 2})

    def test_stale_callback_cannot_enter_new_epoch(self):
        worker, _, captures, _, _ = self.fixture()
        worker.command({"command": "arm", "epoch": 1})
        worker.command({"command": "arm", "epoch": 2})
        captures[0].frame()
        self.assertEqual(worker.frames.qsize(), 0)
        self.assertTrue(captures[0].closed)

    def test_disarm_acknowledges_only_after_release(self):
        worker, _, captures, events, actions = self.fixture()
        worker.command({"command": "arm", "epoch": 1})
        captures[0].frame()
        worker.command({"command": "disarm", "epoch": 2})
        self.assertEqual(actions[-2:], ["close", "disarmed"])
        self.assertEqual(events[-1], {"event": "disarmed", "epoch": 2})
        self.assertEqual(worker.frames.qsize(), 0)

    def test_close_failure_never_emits_wake(self):
        worker, _, captures, events, _ = self.fixture([.9, .9])
        worker.command({"command": "arm", "epoch": 1})
        captures[0].fail_close = True
        for _ in range(2):
            captures[0].frame()
            worker.tick()
        self.assertTrue(worker.fatal)
        self.assertEqual(events[-1]["code"], "capture_release")
        self.assertNotIn("wake", [e["event"] for e in events])

    def test_close_failure_never_acknowledges_disarm(self):
        worker, _, captures, events, _ = self.fixture()
        worker.command({"command": "arm", "epoch": 1})
        captures[0].fail_close = True
        worker.command({"command": "disarm", "epoch": 2})
        self.assertEqual(events[-1], {"event": "error", "epoch": 1, "code": "capture_release"})
        self.assertNotIn("disarmed", [e["event"] for e in events])

    def test_start_failure_releases_and_fails_closed(self):
        worker, _, captures, events, actions = self.fixture()
        factory = worker.capture_factory

        def failing_factory(callback):
            capture = factory(callback)
            capture.fail_start = True
            return capture

        worker.capture_factory = failing_factory
        worker.command({"command": "arm", "epoch": 1})
        self.assertTrue(captures[0].closed)
        self.assertTrue(worker.fatal)
        self.assertEqual(actions[-2:], ["close", "error"])
        self.assertEqual(events[-1]["code"], "capture_start")

    def test_reset_failure_cannot_open_capture(self):
        worker, predictor, captures, events, _ = self.fixture()
        predictor.reset = lambda: (_ for _ in ()).throw(RuntimeError("fixture"))
        worker.command({"command": "arm", "epoch": 1})
        self.assertEqual(captures, [])
        self.assertTrue(worker.fatal)
        self.assertEqual(events[-1]["event"], "error")

    def test_prediction_failure_releases_capture(self):
        worker, _, captures, events, _ = self.fixture([])
        worker.command({"command": "arm", "epoch": 1})
        captures[0].frame()
        worker.tick()
        self.assertTrue(captures[0].closed)
        self.assertEqual(events[-1]["code"], "prediction")

    def test_invalid_prediction_fails_closed(self):
        for value in [float("nan"), float("inf"), -1, 1.5]:
            with self.subTest(value=value):
                worker, _, captures, events, _ = self.fixture([value])
                worker.command({"command": "arm", "epoch": 1})
                captures[0].frame()
                worker.tick()
                self.assertTrue(worker.fatal)
                self.assertTrue(captures[0].closed)

    def test_audio_overflow_is_bounded_and_resets_streak(self):
        worker, predictor, captures, events, _ = self.fixture([.9, .9])
        worker.command({"command": "arm", "epoch": 1})
        captures[0].frame()
        worker.tick()
        for _ in range(100):
            captures[0].frame()
        self.assertEqual(worker.frames.qsize(), 8)
        worker.tick()
        self.assertEqual(worker.frames.qsize(), 0)
        self.assertEqual(worker.hits, 0)
        self.assertEqual(predictor.resets, 2)
        captures[0].frame()
        worker.tick()
        self.assertNotIn("wake", [e["event"] for e in events])

    def test_portaudio_status_and_bad_frame_reset_context(self):
        for data, frames, status in [(None, wake.FRAME_SAMPLES, True), (b"x", wake.FRAME_SAMPLES, False),
                                    (None, 1, False)]:
            with self.subTest(frames=frames, status=status):
                worker, predictor, captures, _, _ = self.fixture()
                worker.command({"command": "arm", "epoch": 1})
                captures[0].frame(data, status=status, frames=frames)
                worker.tick()
                self.assertEqual(predictor.resets, 2)
                self.assertEqual(worker.frames.qsize(), 0)

    def test_stale_epoch_disarms_and_permanently_fails_closed(self):
        worker, _, captures, events, _ = self.fixture()
        worker.command({"command": "arm", "epoch": 4})
        worker.command({"command": "arm", "epoch": 3})
        worker.command({"command": "arm", "epoch": 5})
        self.assertTrue(captures[0].closed)
        self.assertEqual(len(captures), 1)
        self.assertEqual(events[-1]["code"], "stale_epoch")

    def test_invalid_commands_never_open_capture(self):
        records = [None, [], {}, {"command": "arm", "epoch": True}, {"command": "arm", "epoch": 0},
                   {"command": "arm", "epoch": 1.0}, {"command": "arm", "epoch": wake.MAX_EPOCH + 1},
                   {"command": "arm", "epoch": 1, "extra": 1}, {"command": "unknown", "epoch": 1}]
        for record in records:
            with self.subTest(record=record):
                worker, _, captures, events, _ = self.fixture()
                worker.command(record)
                self.assertTrue(worker.fatal)
                self.assertEqual(captures, [])
                self.assertEqual(events[-1]["code"], "invalid_command")

    def test_quit_releases_and_prevents_rearm(self):
        worker, _, captures, events, _ = self.fixture()
        worker.command({"command": "arm", "epoch": 1})
        worker.command({"command": "quit", "epoch": 2})
        worker.command({"command": "arm", "epoch": 3})
        self.assertTrue(worker.done)
        self.assertTrue(captures[0].closed)
        self.assertEqual(len(captures), 1)
        self.assertEqual(events[-1], {"event": "disarmed", "epoch": 2})

    def test_eof_does_not_process_queued_arm(self):
        worker, _, captures, events, _ = self.fixture()
        result = wake.serve(worker, io.StringIO('{"command":"arm","epoch":1}\n'))
        self.assertEqual(result, 0)
        self.assertEqual(captures, [])
        self.assertEqual(events[0], {"event": "ready", "epoch": 0})

    def test_parent_eof_releases_an_armed_capture(self):
        worker, _, captures, _, _ = self.fixture()
        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd, "r")
        writer = os.fdopen(write_fd, "w")
        serving = threading.Thread(target=wake.serve, args=(worker, source))
        serving.start()
        try:
            writer.write('{"command":"arm","epoch":1}\n')
            writer.flush()
            limit = time.monotonic() + 2
            while not worker.armed and time.monotonic() < limit:
                time.sleep(.005)
            self.assertTrue(worker.armed)
            writer.close()
            serving.join(2)
            self.assertFalse(serving.is_alive())
            self.assertTrue(captures[0].closed)
        finally:
            if not writer.closed:
                writer.close()
            serving.join(2)
            source.close()

    def test_check_enumerates_but_cannot_open_or_play_audio(self):
        fake_audio = types.ModuleType("sounddevice")
        fake_audio.query_devices = lambda: [{"name": "fixture", "hostapi": 0, "max_input_channels": 1,
                                            "default_samplerate": 48000}]
        forbidden = lambda *a, **kw: self.fail("check attempted audio API")
        fake_audio.RawInputStream = forbidden
        fake_audio.InputStream = forbidden
        fake_audio.play = forbidden
        fake_predictor = FakePredictor([0] * 50)
        output = io.StringIO()
        with patch.dict("sys.modules", {"sounddevice": fake_audio}), patch.object(wake, "Predictor", return_value=fake_predictor), patch("sys.stdout", output):
            wake.check({"wakeword": 1, "embedding": 2, "melspectrogram": 3})
        result = json.loads(output.getvalue())
        self.assertFalse(result["microphone_opened"])
        self.assertEqual(result["silence_frames"], 50)
        self.assertEqual(result["input_devices"][0]["name"], "fixture")

    def test_model_size_and_digest_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.tflite").write_bytes(b"abc")
            for size, digest in [(4, "x" * 64), (3, "0" * 64)]:
                manifest = root / "manifest.json"
                manifest.write_text(json.dumps({"models": [{"role": "wakeword", "filename": "fixture.tflite",
                                                             "size": size, "sha256": digest}]}))
                with self.assertRaises(ValueError):
                    wake.verified_models(root, manifest)


class ReaderTests(unittest.TestCase):
    def test_binary_pipe_commands_are_accepted(self):
        eof, invalid = threading.Event(), threading.Event()
        commands = queue.Queue(maxsize=2)
        wake.read_commands(io.BytesIO(b'{"command":"disarm","epoch":7}\n'), commands, eof, invalid)
        self.assertTrue(eof.is_set())
        self.assertFalse(invalid.is_set())
        self.assertEqual(commands.get_nowait(), {"command": "disarm", "epoch": 7})

    def test_bad_json_long_lines_and_missing_newline_fail_closed(self):
        for data in ["not-json\n", "x" * (wake.MAX_LINE + 1) + "\n", '{"command":"arm","epoch":1}']:
            eof, invalid = threading.Event(), threading.Event()
            commands = queue.Queue(maxsize=1)
            wake.read_commands(io.StringIO(data), commands, eof, invalid)
            self.assertTrue(eof.is_set())
            self.assertTrue(invalid.is_set())

    def test_command_queue_is_bounded_and_overflow_fails_closed(self):
        eof, invalid = threading.Event(), threading.Event()
        commands = queue.Queue(maxsize=1)
        wake.read_commands(io.StringIO('{"command":"arm","epoch":1}\n' * 100), commands, eof, invalid)
        self.assertTrue(invalid.is_set())
        self.assertEqual(commands.qsize(), 1)

    def test_quit_exits_with_parent_stdin_still_open(self):
        # Exercise real interpreter shutdown while the reader thread is blocked
        # on an OS pipe. All capture/predictor objects remain synthetic.
        source = (
            "import sys; sys.path.insert(0, " + repr(str(Path(__file__).parent)) + "); "
            "import wake_worker as w; from test_wake_worker import FakePredictor; "
            "worker=w.Worker(FakePredictor(), lambda callback: None, w.emit); "
            "raise SystemExit(w.serve(worker, sys.stdin.buffer.raw))"
        )
        child = subprocess.Popen([sys.executable, "-B", "-c", source], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(json.loads(child.stdout.readline()), {"event": "ready", "epoch": 0})
            child.stdin.write('{"command":"quit","epoch":1}\n')
            child.stdin.flush()
            self.assertEqual(json.loads(child.stdout.readline()), {"event": "disarmed", "epoch": 1})
            self.assertEqual(child.wait(timeout=3), 0)
            self.assertEqual(child.stderr.read(), "")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()


if __name__ == "__main__":
    unittest.main()
