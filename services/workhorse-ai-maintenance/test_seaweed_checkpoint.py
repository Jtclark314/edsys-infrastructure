"""Disposable restore networking must avoid public ranges and live overlaps."""

import unittest

from seaweed_checkpoint import validate_network_subnet


class RestoreNetworkContracts(unittest.TestCase):
    def test_public_reserved_ipv6_and_too_small_inputs_are_refused(self):
        for value in (
            "8.8.8.0/24",
            "192.0.2.0/24",
            "fd00::/64",
            "10.0.0.0/31",
            "10.0.0.1/28",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_network_subnet(value, [], [])

    def test_existing_docker_overlap_is_refused_in_both_directions(self):
        for value in ("10.254.248.0/28", "10.254.0.0/16"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_network_subnet(value, ["10.254.248.0/24"], [])

    def test_connected_and_host_address_routes_are_refused(self):
        for route in ("10.254.249.0/24", "10.254.249.4/32"):
            with self.subTest(route=route), self.assertRaises(ValueError):
                validate_network_subnet("10.254.249.0/28", [], [route])

    def test_adjacent_network_and_default_routes_do_not_block(self):
        self.assertEqual(
            validate_network_subnet(
                "10.254.249.0/28",
                ["10.254.248.0/24", "172.18.0.0/16"],
                ["default", "0.0.0.0/0", "192.168.1.0/24", "100.64.0.0/10"],
            ),
            "10.254.249.0/28",
        )


if __name__ == "__main__":
    unittest.main()
