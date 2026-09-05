import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('guard', Path(__file__).parents[1] / 'edsys_3d.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


class GuardTests(unittest.TestCase):
    def test_identity(self):
        cfg = {'mount': '/mnt/test', 'uuid': 'synthetic-uuid', 'fstype': 'ext4'}
        good = {'target': '/mnt/test', 'uuid': 'synthetic-uuid', 'fstype': 'ext4', 'options': 'rw,noatime'}
        g.validate_record(cfg, good)
        for bad in ({}, dict(good, target='/'), dict(good, uuid='wrong'),
                    dict(good, fstype='tmpfs'), dict(good, options='ro,noatime')):
            with self.assertRaises(RuntimeError):
                g.validate_record(cfg, bad)

    def test_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'escape').symlink_to('/tmp')
            with self.assertRaises(RuntimeError):
                g.contained(root, 'escape/not-created', create=True)
            with self.assertRaises(RuntimeError):
                g.contained(root, '../outside', create=True)

    def test_storage_refusal_precedes_app_setup(self):
        with mock.patch.object(g, 'storage', side_effect=RuntimeError('missing')):
            with mock.patch.object(g, 'environment') as setup:
                with self.assertRaises(RuntimeError):
                    g.main(['blender'])
                setup.assert_not_called()

    def test_environment_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(g.os.environ, {'PYTHONPATH':'/untrusted',
                                               'LD_PRELOAD':'/untrusted',
                                               'WAYLAND_DISPLAY':'wayland-0'}):
                env = g.environment(root, 'test')
                self.assertNotIn('PYTHONPATH', env)
                self.assertNotIn('LD_PRELOAD', env)
                self.assertNotIn('WAYLAND_DISPLAY', env)
                for key in ['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME',
                            'XDG_CACHE_HOME','TMPDIR']:
                    self.assertTrue(Path(env[key]).is_relative_to(root))
                self.assertEqual(env['LIBGL_ALWAYS_SOFTWARE'], '1')
                self.assertEqual(env['PYTHONNOUSERSITE'], '1')
                gpu = g.environment(root, 'test', gpu=True)
                self.assertNotIn('LIBGL_ALWAYS_SOFTWARE', gpu)


if __name__ == '__main__':
    unittest.main()
