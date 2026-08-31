from pathlib import Path


DEPLOY = Path(__file__).parents[2] / "ops" / "deploy-netdata-compute.sh"


def test_stream_secret_never_uses_scp_or_ordinary_tmp():
    source = DEPLOY.read_text(encoding="utf-8")
    assert ":/tmp/stream.conf.edsys" not in source
    assert "/tmp/stream.conf.edsys" not in source
    assert "scp \"${scp_options[@]}\" \"${tmpdir}/${child}-stream.conf\"" not in source
    assert "scp \"${scp_options[@]}\" \"${tmpdir}/${satellite}-stream.conf\"" not in source


def test_every_child_uses_root_private_run_staging_and_atomic_install():
    source = DEPLOY.read_text(encoding="utf-8")
    assert source.count("mktemp /run/edsys-netdata-stream.XXXXXX") == 2
    assert source.count("set -Eeuo pipefail; umask 077") == 2
    assert source.count("root:root:600") == 4
    assert source.count("install -m 0600 -o root -g root") >= 3
    assert source.count("mv -fT --") >= 2
    assert source.count("trap cleanup_secret EXIT") == 2


def test_secret_content_is_streamed_only_over_ssh_standard_input():
    source = DEPLOY.read_text(encoding="utf-8")
    assert '<"${tmpdir}/${child}-stream.conf"' in source
    assert '<"${tmpdir}/${satellite}-stream.conf"' in source


def test_current_children_are_explicit_and_strictly_host_key_checked():
    source = DEPLOY.read_text(encoding="utf-8")
    assert "pve_children=(pve-node0 pve-node1 pve-node2 pve-node3)" in source
    assert "satellites=(netbox)" in source
    assert "192.168.50.51 192.168.50.52 192.168.50.53 192.168.50.54 192.168.50.81" in source
    assert "UserKnownHostsFile=${operator_home}/.ssh/known_hosts" in source
    assert "StrictHostKeyChecking=no" not in source
    assert "StrictHostKeyChecking=accept-new" not in source


def test_deployer_does_not_mutate_operator_known_hosts():
    source = DEPLOY.read_text(encoding="utf-8")
    assert "ssh-keyscan" not in source
    assert "operator-known_hosts.before" not in source
    assert "known_hosts_mutated" not in source
