#!/usr/bin/env python3
"""Own the two private app stacks and their explicitly named stateless helpers."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path('/mnt/ai-store/edsys-private-services')
SOURCE = Path('/srv/edsys/edsys-infrastructure/docker')
UUID_FILE = Path('/etc/edsys-private-web-apps/mount-uuid')
APPS = (('stirling-pdf', 'edsys-stirling-pdf'), ('changedetection', 'edsys-changedetection'))
STOP_ORDER = ('edsys-changedetection', 'edsys-changedetection-browser',
              'edsys-changedetection-egress', 'edsys-stirling-pdf')
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']


def check_mount():
    expected = UUID_FILE.read_text().strip()
    if not expected or not Path('/mnt/ai-store').is_mount():
        raise RuntimeError('Required private-services disk is not mounted')
    actual = subprocess.check_output(
        ['findmnt', '--noheadings', '--output', 'UUID', '--mountpoint', '/mnt/ai-store'],
        text=True).strip()
    if actual != expected:
        raise RuntimeError('Private-services disk UUID does not match the installed guard')
    if not ROOT.is_dir():
        raise RuntimeError('Private-services data root is absent')


def compose(app, project):
    return [*DOCKER, 'compose', '--ansi', 'never', '--project-name', project,
            '--env-file', str(ROOT / 'deploy' / (app + '.env')),
            '--file', str(SOURCE / app / 'compose.yaml')]


def stop_apps():
    # Enumerate first so a daemon failure is not mistaken for absent containers.
    names = set(subprocess.check_output(
        [*DOCKER, 'ps', '--all', '--format', '{{.Names}}'], text=True, timeout=15).splitlines())
    failures = []
    for name in STOP_ORDER:
        if name not in names:
            continue
        try:
            subprocess.run([*DOCKER, 'stop', '--time', '45', name], check=True, timeout=65)
        except (subprocess.SubprocessError, OSError):
            failures.append(name)
    if failures:
        raise RuntimeError('Could not stop owned applications: ' + ', '.join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check-mount', 'start', 'stop', 'status'))
    args = parser.parse_args()
    if args.action in ('check-mount', 'start'):
        check_mount()
    if args.action == 'check-mount':
        print('Private-services disk guard passed')
        return
    # Stop does not read Compose or a possibly lost mount: stop existing names only.
    if args.action == 'stop':
        stop_apps()
        return
    try:
        for app, project in APPS:
            command = compose(app, project)
            command += (['up', '--detach', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '180']
                        if args.action == 'start' else ['ps', '--format', 'json'])
            subprocess.run(command, check=True, timeout=210)
    except BaseException:
        if args.action == 'start':
            stop_apps()
        raise


if __name__ == '__main__':
    main()
