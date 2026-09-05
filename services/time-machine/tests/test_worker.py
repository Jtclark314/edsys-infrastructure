import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("time_machine_worker", Path(__file__).resolve().parents[1] / "time_machine.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerBoundaryTests(unittest.TestCase):
    def test_lab_rejects_paths_and_non_owned_targets(self):
        for value in ["../production", "", "a" * 33, "/tmp/lab"]:
            with self.assertRaises(ValueError):
                worker.Lab(value)
        lab = worker.Lab("a" * 32)
        with self.assertRaises(RuntimeError):
            lab.remove_container("edsys-ai-portal")
        with patch.object(worker, "inspect", return_value={"Config": {"Labels": {worker.LABEL: "another-run"}}}):
            with self.assertRaises(RuntimeError):
                lab.remove_container(lab.app)

    def test_subnet_avoids_docker_and_host_routes(self):
        def command(args):
            if args[:3] == ["docker", "network", "ls"]:
                result = "test-network"
            elif args[:3] == ["docker", "network", "inspect"]:
                result = json.dumps([{"IPAM": {"Config": [{"Subnet": "10.251.240.0/28"}]}}])
            else:
                result = json.dumps([{"dst": "default"}, {"dst": "10.251.240.16/28"}])
            return SimpleNamespace(stdout=result)
        with patch.object(worker, "command", side_effect=command):
            self.assertEqual(worker.lab_subnet(), "10.251.240.32/28")

    def test_requests_cannot_supply_commands_or_arbitrary_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requests").mkdir()
            for i, value in enumerate([
                {"id": "a" * 32, "scenario": "dns", "command": "unexpected"},
                {"id": "b" * 32, "scenario": "restart-production"},
                {"id": "../escape", "scenario": "dns"},
            ]):
                (root / "requests" / f"{chr(97 + i) * 32}.json").write_text(json.dumps(value))
            with patch.object(worker, "STATE", root), patch.object(worker, "run_rehearsal") as run:
                worker.process_requests()
                run.assert_not_called()

    def test_interrupted_run_is_cleaned_without_replaying_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = {"id": "a" * 32, "scenario": "storage"}
            worker.write_json(root / "requests" / (value["id"] + ".json"), value)
            worker.write_json(root / "results" / (value["id"] + ".json"), {**value, "status": "running"})
            with patch.object(worker, "STATE", root), patch.object(worker, "run_rehearsal") as run, patch.object(worker.Lab, "cleanup") as cleanup:
                worker.process_requests()
                cleanup.assert_called_once()
                run.assert_not_called()
            result = json.loads((root / "results" / (value["id"] + ".json")).read_text())
            self.assertEqual(result["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
