import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("immich_render", Path(__file__).with_name("render_compose.py"))
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class RenderTests(unittest.TestCase):
    def source(self):
        return {"services": {name: {"image": "floating:release", "environment": {"EXISTING_SETTING": "retained"}, "restart": "unless-stopped", "ports": ["0.0.0.0:2283:2283"]} for name in renderer.IMAGES}}

    def test_candidate_retains_settings_without_publishing_or_external_network(self):
        source = self.source()
        before = copy.deepcopy(source)
        rendered = renderer.render(source, Path("/state"), Path("/photos"), True)
        self.assertEqual(source, before)
        self.assertTrue(rendered["networks"]["default"]["internal"])
        for name, service in rendered["services"].items():
            self.assertEqual(service["image"], renderer.IMAGES[name])
            self.assertEqual(service["restart"], "no")
            self.assertEqual(service["pull_policy"], "never")
            expected_env = copy.deepcopy(before["services"][name]["environment"])
            if name == "immich-server":
                expected_env["IMMICH_MEDIA_LOCATION"] = "/usr/src/app/upload"
            self.assertEqual(service["environment"], expected_env)
            self.assertNotIn("ports", service)
            for volume in service["volumes"]:
                self.assertFalse(volume["bind"]["create_host_path"])

    def test_final_only_binds_reviewed_addresses(self):
        rendered = renderer.render(self.source(), Path("/state"), Path("/photos"), False)
        ports = rendered["services"]["immich-server"]["ports"]
        self.assertEqual({row["host_ip"] for row in ports}, {"127.0.0.1", "192.168.50.50"})
        self.assertEqual({row["published"] for row in ports}, {"2283"})
        self.assertFalse(rendered["networks"]["default"]["internal"])
        server = rendered["services"]["immich-server"]
        self.assertEqual(server["environment"]["IMMICH_MEDIA_LOCATION"], "/usr/src/app/upload")
        self.assertEqual(next(v["source"] for v in server["volumes"]
                              if v["target"] == "/usr/src/app/upload"), "/photos/upload")

    def test_unknown_source_services_are_refused(self):
        source = self.source()
        source["services"]["unreviewed"] = {}
        with self.assertRaises(ValueError):
            renderer.render(source, Path("/state"), Path("/photos"), False)


if __name__ == "__main__":
    unittest.main()
