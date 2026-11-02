import subprocess
import unittest
from unittest.mock import patch
import control


class LifecycleTests(unittest.TestCase):
    @patch.object(control, 'check_mount')
    def test_partial_stack_start_failure_also_stops_stateless_helpers(self, guard):
        failure = subprocess.CalledProcessError(1, ['fixture'])
        names = '\n'.join((*control.STOP_ORDER, 'unrelated-container'))
        with patch('sys.argv', ['control', 'start']), patch.object(control.subprocess, 'run',
                side_effect=[None, failure, None, None, None, None]) as run, patch.object(
                    control.subprocess, 'check_output', return_value=names):
            with self.assertRaises(subprocess.CalledProcessError):
                control.main()
        guard.assert_called_once()
        self.assertEqual([call.args[0][-1] for call in run.call_args_list[2:]], list(control.STOP_ORDER))

    def test_stop_without_mount_or_compose_stops_only_all_owned_names(self):
        names = '\n'.join((*control.STOP_ORDER, 'unrelated-container'))
        with patch('sys.argv', ['control', 'stop']), patch.object(control, 'check_mount') as guard, \
                patch.object(control, 'compose') as compose, patch.object(control.subprocess, 'check_output',
                return_value=names), patch.object(control.subprocess, 'run') as run:
            control.main()
        guard.assert_not_called()
        compose.assert_not_called()
        self.assertEqual([call.args[0][-1] for call in run.call_args_list], list(control.STOP_ORDER))

    def test_one_helper_stop_failure_does_not_skip_remaining_owned_containers(self):
        failure = subprocess.CalledProcessError(1, ['fixture'])
        with patch.object(control.subprocess, 'check_output', return_value='\n'.join(control.STOP_ORDER)), \
                patch.object(control.subprocess, 'run', side_effect=[None, failure, None, None]) as run:
            with self.assertRaisesRegex(RuntimeError, 'edsys-changedetection-browser'):
                control.stop_apps()
        self.assertEqual([call.args[0][-1] for call in run.call_args_list], list(control.STOP_ORDER))

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
