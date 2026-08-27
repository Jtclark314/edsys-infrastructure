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


def test_automation_first_enrollment_is_explicit_and_fingerprint_gated():
    source = DEPLOY.read_text(encoding="utf-8")
    assert 'automation_ip="192.168.50.82"' in source
    assert "--automation-host-key" in source
    assert "ssh-keyscan -T 5 -t ed25519" in source
    assert "SHA256:[A-Za-z0-9+/]{43}" in source
    assert "The live ${automation_ip} ED25519 host key did not match" in source
    assert "StrictHostKeyChecking=no" not in source
    assert "StrictHostKeyChecking=accept-new" not in source


def test_known_hosts_change_is_private_backed_up_and_rolled_back():
    source = DEPLOY.read_text(encoding="utf-8")
    assert "operator-known_hosts.before" in source
    assert "known_hosts_mutated=1" in source
    assert "restoring the operator SSH known_hosts file" in source
    assert 'install -m 0600 -o "$operator_user"' in source
