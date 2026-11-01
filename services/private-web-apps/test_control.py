import subprocess
import unittest
from unittest.mock import patch
import control


class LifecycleTests(unittest.TestCase):
    @patch.object(control, 'check_mount')
    def test_second_start_failure_stops_both_owned_apps(self, guard):
        failure = subprocess.CalledProcessError(1, ['fixture'])
        with patch('sys.argv', ['control', 'start']), patch.object(control.subprocess, 'run',
                side_effect=[None, failure, None, None]) as run, patch.object(
                    control.subprocess, 'check_output', return_value='edsys-stirling-pdf\nedsys-changedetection\nunrelated\n'):
            with self.assertRaises(subprocess.CalledProcessError):
                control.main()
        guard.assert_called_once()
        self.assertEqual([x.args[0][-1] for x in run.call_args_list[2:]],
                         ['edsys-changedetection', 'edsys-stirling-pdf'])

    def test_one_stop_failure_still_attempts_the_other_app(self):
        failure = subprocess.CalledProcessError(1, ['fixture'])
        with patch.object(control.subprocess, 'check_output',
                return_value='edsys-stirling-pdf\nedsys-changedetection\nunrelated\n'), patch.object(
                    control.subprocess, 'run', side_effect=[failure, None]) as run:
            with self.assertRaisesRegex(RuntimeError, 'edsys-changedetection'):
                control.stop_apps()
        self.assertEqual([x.args[0][-1] for x in run.call_args_list],
                         ['edsys-changedetection', 'edsys-stirling-pdf'])

    def test_bad_mount_refuses_all_docker_operations(self):
        with patch('sys.argv', ['control', 'start']), patch.object(control, 'check_mount',
                side_effect=RuntimeError('wrong disk')), patch.object(control.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'wrong disk'):
                control.main()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
