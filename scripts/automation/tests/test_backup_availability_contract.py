from pathlib import Path


AUTOMATION_ROOT = Path(__file__).parents[1]
SERVICE = AUTOMATION_ROOT / "systemd" / "edsys-edcore-automation-backup-pull.service"
TIMER = AUTOMATION_ROOT / "systemd" / "edsys-edcore-automation-backup-pull.timer"
README = AUTOMATION_ROOT / "README.md"
HEALTHCHECKS_BOOTSTRAP = AUTOMATION_ROOT.parent / "ops" / "bootstrap-healthchecks.sh"


def test_global_restic_has_no_automation_pull_dependency():
    service = SERVICE.read_text(encoding="utf-8")
    assert "edsys-backup.service" not in service
    assert not (AUTOMATION_ROOT / "systemd" / "40-edcore-automation-backup-pull.conf").exists()


def test_pull_remains_independently_scheduled_before_restic():
    timer = TIMER.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 01:55:00 America/New_York" in timer
    assert "Persistent=true" in timer


def test_pull_has_a_separate_freshness_signal():
    service = SERVICE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert (
        "EnvironmentFile=-/etc/edsys-healthchecks/edsys-edcore-automation-backup-pull.env"
        in service
    )
    assert "26-hour timeout" in readme
    assert "global Restic still protects the last verified immutable copy" in readme


def test_healthchecks_bootstrap_reconciles_pull_freshness_and_service_mapping():
    bootstrap = HEALTHCHECKS_BOOTSTRAP.read_text(encoding="utf-8")
    assert (
        '("edsys-edcore-automation-backup-pull", '
        '"EdCore Automation verified backup pull", 26, 2)'
        in bootstrap
    )
    assert (
        "[edsys-edcore-automation-backup-pull]=edsys-edcore-automation-backup-pull"
        in bootstrap
    )
    assert "if check.timeout != desired_timeout:" in bootstrap
    assert "if check.grace != desired_grace:" in bootstrap


def test_root_timer_never_executes_the_user_owned_checkout():
    service = SERVICE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/libexec/edsys-edcore-automation/pull-backup.sh" in service
    assert (
        "ExecStartPre=/usr/local/libexec/edsys-edcore-automation/validate-installed-pull.py"
        in service
    )
    assert "ExecStart=/srv/edsys/edsys-infrastructure" not in service
    assert "install -m 0755 -o root -g root scripts/automation/pull-backup.sh" in readme
    assert "operator-owned Git checkout" in readme


def test_pull_uses_only_the_dedicated_forced_command_identity():
    pull = (AUTOMATION_ROOT / "pull-backup.sh").read_text(encoding="utf-8")
    wrapper = (AUTOMATION_ROOT / "guest-backup-ssh.sh").read_text(encoding="utf-8")
    exporter = (AUTOMATION_ROOT / "guest-backup-export.py").read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert 'readonly remote_user="edsys-backup"' in pull
    assert 'readonly secret_root="/etc/edsys-secrets/edcore-automation-backup"' in pull
    assert "operator_home" not in pull
    assert "/home/jeremy" not in pull
    assert "rsync" not in pull
    assert "sudo -n rsync" not in pull
    assert "edsys-backup-current" in pull
    assert "edsys-backup-export" in pull
    assert "${SSH_ORIGINAL_COMMAND:-}" in wrapper
    assert "\neval " not in wrapper
    assert "forced command is not permitted" in exporter
    assert "NOPASSWD: /usr/local/libexec/edsys-edcore-automation-backup-export *" in readme
    assert "never invokes a shell" in readme.lower()
    for option in (
        "IdentityFile=none",
        "IdentitiesOnly=yes",
        "IdentityAgent=none",
        "GlobalKnownHostsFile=/dev/null",
        "StrictHostKeyChecking=yes",
        "PasswordAuthentication=no",
        "ProxyCommand=none",
        "ClearAllForwardings=yes",
    ):
        assert option in pull


def test_provisioners_create_only_an_isolated_pin_and_forced_reader():
    client = (AUTOMATION_ROOT / "provision-backup-client.sh").read_text(encoding="utf-8")
    guest = (AUTOMATION_ROOT / "provision-guest-backup-reader.sh").read_text(
        encoding="utf-8"
    )
    assert 'readonly secret_dir="${secret_parent}/edcore-automation-backup"' in client
    assert 'readonly host_key_alias="edcore-automation-backup"' in client
    assert "ssh-keyscan" not in client
    assert 'readonly public_file="${identity_file}.pub"' in client
    assert 'readonly account="edsys-backup"' in guest
    assert "--system --user-group" in guest
    assert "passwd --lock" in guest
    assert 'install -d -o root -g "$primary_gid" -m 0750 "$account_home/.ssh"' in guest
    assert 'chown root:"$primary_gid" "$authorized_tmp"' in guest
    assert 'chmod 0640 "$authorized_tmp"' in guest
    assert "restrict,no-user-rc,command=" in guest
    assert "supplementary groups" in guest
    assert "NOPASSWD: %s *" in guest
    assert "NOPASSWD: ALL" not in guest
    assert " ALL=(ALL)" not in guest
    assert "AllowUsers jeremy edsys-backup" in guest
    assert "Match User edsys-backup" in guest
    assert "AuthenticationMethods publickey" in guest
    assert "PasswordAuthentication no" in guest
    assert "KbdInteractiveAuthentication no" in guest
    assert "DisableForwarding yes" in guest
    assert "ForceCommand $launcher" in guest
    assert "PermitRootLogin" not in guest
    assert "sshd -t" in guest
    assert "systemctl reload ssh" in guest
    assert "effective AllowUsers is broader or narrower" in guest


def test_root_copy_install_and_preflight_cover_every_executable():
    service = SERVICE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for name in (
        "pull-backup.sh",
        "verify-backup.py",
        "extract-backup.py",
        "validate-installed-pull.py",
    ):
        assert f"scripts/automation/{name}" in readme
        assert f"/usr/local/libexec/edsys-edcore-automation/{name}" in readme
    assert "ExecStartPre=" in service
    assert "before enabling the timer" in readme


def test_private_healthchecks_url_is_not_passed_in_curl_argv():
    pull = (AUTOMATION_ROOT / "pull-backup.sh").read_text(encoding="utf-8")
    assert "curl -q --config -" in pull
    assert 'noproxy = "*"' in pull
    assert 'curl -fsS --max-time "${HC_TIMEOUT_SECONDS:-10}"' not in pull
    assert "private URL is never placed in curl's process arguments" in README.read_text(
        encoding="utf-8"
    )
