from pathlib import Path


def test_operator_policy_and_packaged_policy_are_identical():
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "fleet-policy.yml").read_bytes() == (
        root / "edsys_fleet" / "fleet-policy.yml"
    ).read_bytes()
