import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('guard',Path(__file__).resolve().parents[1]/'guard.py')
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
class GuardTests(unittest.TestCase):
    def test_guest_has_only_host_peer_and_loopback_admin(self):
        text=guard.render('guest','192.168.77.1')
        self.assertIn('iifname "eth0" ip saddr 192.168.77.1',text)
        self.assertLess(text.index('tcp dport 47990 counter drop'),text.index('ip saddr'))
        self.assertNotIn('forward',text)
    def test_relay_uses_exact_tailnet_peer(self):
        text=guard.render('relay','100.64.1.2',True)
        self.assertTrue(text.startswith('delete table inet edsys_kali_stream\n'))
        self.assertIn('iifname "tailscale0" ip saddr 100.64.1.2',text)
        self.assertNotIn('flush ruleset',text)
        self.assertIn('udp dport { 47998, 47999, 48000, 48002, 48010 } counter drop',text)
    def test_rejects_networks_ipv6_and_rule_injection(self):
        for peer in ('0.0.0.0/0','::1','100.64.1.2; accept'):
            with self.assertRaises(ValueError): guard.render('relay',peer)
        with self.assertRaises(ValueError): guard.render('other','192.168.77.1')
if __name__=='__main__': unittest.main()
