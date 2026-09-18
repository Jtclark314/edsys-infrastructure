"""Offline tests: synthetic XML and mocked commands; no network traffic."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'kali_network_review.py'
spec = importlib.util.spec_from_file_location('review', SOURCE)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def fixture(detail=False):
    if detail:
        ports = '''<port protocol="tcp" portid="443"><state state="open" reason="syn-ack"/>
        <service name="http" tunnel="ssl" method="probed" product="Example" version="1"/>
        <script id="ssl-cert" output="Synthetic certificate"/></port>'''
    else:
        ports = '''<port protocol="tcp" portid="443"><state state="open" reason="syn-ack"/>
        <service name="https" method="table"/></port>
        <port protocol="tcp" portid="9100"><state state="open" reason="syn-ack"/>
        <service name="jetdirect" method="table"/></port>
        <port protocol="tcp" portid="22"><state state="closed" reason="conn-refused"/></port>'''
    return f'''<nmaprun><host><status state="up" reason="user-set"/>
    <address addr="192.168.50.54" addrtype="ipv4"/><ports>{ports}</ports></host>
    <host><status state="up" reason="user-set"/>
    <address addr="192.168.50.55" addrtype="ipv4"/>
    <ports><extraports state="filtered" count="3"/></ports></host>
    <runstats><finished exit="success"/></runstats></nmaprun>'''


class FakeRunner(r.Runner):
    def run(self, argv, name, timeout):
        self.records.append(dict(name=name, argv=argv, status='completed'))
        self.save()
        if name == 'inventory' or name.startswith('detail-'):
            (self.out / (name + '.xml')).write_text(fixture(name != 'inventory'))
        return True


class ReviewTests(unittest.TestCase):
    def test_scope_and_network_edges(self):
        self.assertEqual(len(r.targets_from(['192.168.50.0/24'])), 254)
        self.assertEqual(r.targets_from(['192.168.50.54', '192.168.50.54/32']), ['192.168.50.54'])
        for target in ['8.8.8.8', '192.168.49.0/24', '192.168.50.0/23', '::1',
                       'example.com', '-iL', '192.168.50.0', '192.168.50.255']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                r.targets_from([target])

    def test_port_validation(self):
        self.assertEqual(r.ports_from('22,80-82,22,8006'), [22, 80, 81, 82, 8006])
        for ports in ['0', '65536', '80-22', '1-65535', '22;id', '22,']:
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                r.ports_from(ports)

    def test_dry_run_never_spawns_or_writes(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(r.subprocess, 'Popen', side_effect=AssertionError('Unexpected process')):
            dest = Path(d) / 'absent'
            self.assertEqual(r.main(['--ports', '22,8006', '--output-root', str(dest)]), 0)
            self.assertFalse(dest.exists())

    def test_subnet_detail_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            r.main(['--targets', '192.168.50.0/24', '--ports', '22'])
        self.assertEqual(exc.exception.code, 2)

    def test_xml_distinguishes_assumed_up_and_observed_ports(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'input.xml'
            path.write_text(fixture())
            rows, reported, complete = r.parse_xml(path)
            self.assertTrue(complete)
            self.assertEqual(reported, {'192.168.50.54', '192.168.50.55'})
            self.assertEqual({row['ip'] for row in rows}, {'192.168.50.54'})
            self.assertEqual({row['port'] for row in rows}, {443, 9100})
            self.assertEqual({row['method'] for row in rows}, {'table'})
            path.write_text(fixture().replace('<finished exit="success"/>', ''))
            self.assertFalse(r.parse_xml(path)[2])

    def test_end_to_end_offline_detail_preserves_limits(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(r, 'Runner', FakeRunner), patch.object(r.shutil, 'which', return_value='/mock'), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(r.subprocess, 'Popen', side_effect=AssertionError('Unexpected real process')):
            self.assertEqual(r.main(['--execute', '--targets', '192.168.50.54', '192.168.50.55',
                                     '--ports', '22,443,9100', '--output-root', d]), 0)
            out = next(Path(d).iterdir())
            coverage = json.loads((out / 'coverage.json').read_text())
            self.assertEqual(coverage['addresses_with_open_tcp'], ['192.168.50.54'])
            self.assertEqual(coverage['targets_without_observed_open_tcp'], ['192.168.50.55'])
            commands = json.loads((out / 'commands.json').read_text())
            self.assertEqual(len(commands), 4)
            detailed = commands[1]['argv']
            self.assertEqual(detailed[detailed.index('-p') + 1], '443')
            self.assertIn('-sT', detailed)
            self.assertIn('--unprivileged', detailed)
            self.assertNotIn('vuln', detailed[detailed.index('--script') + 1])
            self.assertEqual(commands[2]['argv'][:2], ['curl', '-q'])
            self.assertNotIn('-k', commands[2]['argv'])
            self.assertNotIn('-L', commands[2]['argv'])
            services = json.loads((out / 'services.json').read_text())
            self.assertEqual(services[0]['method'], 'probed')
            self.assertEqual(services[1]['method'], 'table')
            self.assertIn('Run ended', (out / 'README.txt').read_text())

    def test_missing_target_prevents_complete_claim(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(r, 'Runner', FakeRunner), patch.object(r.shutil, 'which', return_value='/mock'):
            result = r.main(['--execute', '--profile', 'inventory', '--targets', '192.168.50.0/24',
                             '--ports', '443', '--output-root', d])
            out = next(Path(d).iterdir())
            self.assertEqual(result, 2)
            self.assertFalse(json.loads((out / 'coverage.json').read_text())['discovery_completed'])

    def test_timeout_and_launch_failure_are_recorded(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            runner = r.Runner(Path(d))
            self.assertFalse(runner.run([sys.executable, '-c', 'import time; time.sleep(10)'], 'timeout', .1))
            self.assertEqual(runner.records[-1]['status'], 'timeout')
            self.assertFalse(runner.run(['/nonexistent/network-review-test'], 'missing', 1))
            self.assertEqual(runner.records[-1]['status'], 'failed')
            self.assertIsNone(runner.records[-1]['returncode'])

    def test_untrusted_metadata_is_escaped(self):
        self.assertEqual(r.csv_cell(' =SUM(A1)'), "' =SUM(A1)")
        self.assertNotIn('<script>', r.md('<script>|value\n'))
        self.assertNotIn('|', r.md('a|b'))


if __name__ == '__main__':
    unittest.main()
