import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("renderer_profiles", ROOT / "render_compose.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class RuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        self.source = {"services": {k: {"image": v, "environment": {"EXISTING_SETTING": "retained"}} for k,v in m.IMAGES.items()}}
        self.cpu = {"version": "v3.3.5", "backend": "cpu", "model_revision": "v2"}
        self.cuda = dict(self.cpu, backend="cuda")
    def render(self, profile=None, candidate=False):
        return m.render(self.source, Path("/mnt/ai-store/immich"), Path("/mnt/immich-library"), candidate, profile)
    def test_no_profile_retains_same_images_and_no_gpu(self):
        for name,row in self.render()["services"].items():
            self.assertEqual(row["image"], m.IMAGES[name]); self.assertNotIn("deploy", row)
    def test_cpu_pins_new_application_only(self):
        rows=self.render(self.cpu)["services"]
        self.assertEqual(rows["immich-server"]["image"],m.UPGRADE_IMAGES["server"])
        self.assertEqual(rows["immich-machine-learning"]["image"],m.UPGRADE_IMAGES["cpu"])
        for row in rows.values(): self.assertNotIn("deploy",row)
    def test_cuda_reserves_only_ml_device_zero(self):
        rows=self.render(self.cuda)["services"]
        self.assertEqual(rows["immich-machine-learning"]["image"],m.UPGRADE_IMAGES["cuda"])
        dev=rows["immich-machine-learning"]["deploy"]["resources"]["reservations"]["devices"]
        self.assertEqual(dev,[{"driver":"nvidia","device_ids":["0"],"capabilities":["gpu"]}])
        for name,row in rows.items():
            if name!="immich-machine-learning": self.assertNotIn("deploy",row)
    def test_mount_startup_access_guards_preserved(self):
        rows=self.render(self.cuda)["services"]
        for row in rows.values():
            self.assertEqual(row["restart"],"no");self.assertEqual(row["pull_policy"],"never")
            self.assertTrue(all(v["bind"]["create_host_path"] is False for v in row["volumes"]))
        srv=rows["immich-server"]
        self.assertEqual(srv["environment"]["IMMICH_MEDIA_LOCATION"],"/usr/src/app/upload")
        self.assertEqual(srv["volumes"][0]["source"],"/mnt/immich-library/upload")
        self.assertEqual([p["host_ip"] for p in srv["ports"]],["127.0.0.1","192.168.50.50"])
    def test_candidate_has_no_ports_and_internal_network(self):
        out=self.render(self.cuda,True)
        self.assertTrue(out["networks"]["default"]["internal"])
        self.assertTrue(all("ports" not in row for row in out["services"].values()))
    def test_invalid_profiles_refused(self):
        for profile in (dict(self.cpu, version="latest"),dict(self.cpu,backend="all"),dict(self.cpu,model_revision="main"),dict(self.cpu,arbitrary="value"),{}):
            with self.subTest(profile=profile),self.assertRaises(ValueError): self.render(profile)
    def test_bounded_settings_no_preload_no_model_name_change(self):
        self.source["services"]["immich-machine-learning"]["environment"]["MACHINE_LEARNING_PRELOAD__CLIP"]="unused"
        env=self.render(self.cuda)["services"]["immich-machine-learning"]["environment"]
        for k,v in {"MACHINE_LEARNING_MODEL_REVISION":"v2","MACHINE_LEARNING_WORKERS":"1","MACHINE_LEARNING_REQUEST_THREADS":"1","MACHINE_LEARNING_MODEL_TTL":"60","MACHINE_LEARNING_MODEL_TTL_POLL_S":"10","MACHINE_LEARNING_MAX_BATCH_SIZE__FACIAL_RECOGNITION":"1","MACHINE_LEARNING_MAX_BATCH_SIZE__OCR":"1"}.items(): self.assertEqual(env[k],v)
        self.assertFalse(any(k.startswith("MACHINE_LEARNING_PRELOAD__") for k in env))
        self.assertFalse(any("MODEL_NAME" in k for k in env));self.assertEqual(env["EXISTING_SETTING"],"retained")
    def test_source_not_mutated(self):
        before=copy.deepcopy(self.source);self.render(self.cuda);self.assertEqual(self.source,before)
    def test_database_redis_images_and_environment_retained(self):
        rows=self.render(self.cuda)["services"]
        for name in ("database","redis"):
            self.assertEqual(rows[name]["image"],m.IMAGES[name]);self.assertEqual(rows[name]["environment"],self.source["services"][name]["environment"])
    def test_omitted_profile_cannot_downgrade_an_upgraded_source(self):
        for profile in (self.cpu, self.cuda):
            upgraded=self.render(profile);before=copy.deepcopy(upgraded)
            with self.assertRaisesRegex(ValueError,"explicit_upgrade_profile_required"):
                m.render(upgraded,Path("/state"),Path("/photos"),False)
            self.assertEqual(upgraded,before)
            explicit=m.render(upgraded,Path("/state"),Path("/photos"),False,profile)
            self.assertEqual(explicit["services"]["immich-server"]["image"],m.UPGRADE_IMAGES["server"])
    def test_manifest_only_reference_also_requires_an_explicit_profile(self):
        for role,manifest in (("immich-server","server"),("immich-machine-learning","cpu"),("immich-machine-learning","cuda")):
            upgraded=copy.deepcopy(self.source)
            upgraded["services"][role]["image"]="ghcr.io/immich-app/"+role+"@"+m.UPGRADE_IMAGES[manifest].rsplit("@",1)[-1]
            with self.assertRaisesRegex(ValueError,"explicit_upgrade_profile_required"):
                m.render(upgraded,Path("/state"),Path("/photos"),False)
    def test_tag_only_upgraded_source_requires_an_explicit_profile(self):
        for role,manifest in (("immich-server","server"),("immich-machine-learning","cpu"),("immich-machine-learning","cuda")):
            upgraded=copy.deepcopy(self.source)
            upgraded["services"][role]["image"]=m.UPGRADE_IMAGES[manifest].split("@",1)[0]
            with self.assertRaisesRegex(ValueError,"explicit_upgrade_profile_required"):
                m.render(upgraded,Path("/state"),Path("/photos"),False)

    def test_private_profiles_parse(self):
        for backend in ("cpu","cuda"):
            out=self.render(json.loads((ROOT/"profiles"/f"v3.3.5-{backend}.json").read_text()))
            self.assertEqual(out["services"]["immich-machine-learning"]["image"],m.UPGRADE_IMAGES[backend])

if __name__=="__main__": unittest.main()
