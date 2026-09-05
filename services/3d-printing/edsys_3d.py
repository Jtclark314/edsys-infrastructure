#!/usr/bin/python3
"""Fail-closed launcher for the private AI Store 3D workspace."""
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys

CONFIG = Path('/etc/edsys-3d')
PROJECT_DIRS = ('brief', 'references', 'source', 'exports', 'previews',
                'slicing', 'approved', 'print-results')


def validate_record(config, record):
    if (record.get('target') != config['mount'] or
            record.get('uuid') != config['uuid'] or
            record.get('fstype') != config['fstype'] or
            'rw' not in record.get('options', '').split(',')):
        raise RuntimeError('Expected writable AI Store is missing or has the wrong identity')


def storage():
    config = json.loads((CONFIG / 'storage.json').read_text())
    result = subprocess.run(['/usr/bin/findmnt', '-J', '-M', config['mount'],
                             '-o', 'TARGET,UUID,FSTYPE,OPTIONS'],
                            capture_output=True, text=True, check=True, timeout=5)
    records = json.loads(result.stdout).get('filesystems', [])
    if len(records) != 1:
        raise RuntimeError('Expected exact AI Store mount is unavailable')
    validate_record(config, records[0])
    root = Path(config['workspace'])
    mount = Path(config['mount'])
    if (root.is_symlink() or not root.is_dir() or root.resolve() != mount / '3d-printing'
            or root.stat().st_dev != mount.stat().st_dev
            or root.stat().st_dev == Path('/').stat().st_dev):
        raise RuntimeError('Workspace is not on the expected independent filesystem')
    return root


def contained(root, relative, create=False):
    path = root / relative
    # Refuse symlink escapes, including a symlink in an existing parent.
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Path escapes the guarded workspace')
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists() or path.stat().st_dev != root.stat().st_dev:
        raise RuntimeError('Path is missing or on a different filesystem')
    return path


def environment(root, app, gpu=False):
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'LD_LIBRARY_PATH',
                'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH',
                'WAYLAND_DISPLAY', 'LD_PRELOAD'):
        env.pop(key, None)
    home = contained(root, 'environments/user-data/' + app, create=True)
    cache = contained(root, 'cache/' + app, create=True)
    temporary = contained(root, 'temporary/' + app, create=True)
    for name in ('.config', '.local/share', '.local/state'):
        (home / name).mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
               EDSYS_3D_WORKSPACE=str(root),
               XDG_DATA_HOME=str(home / '.local/share'),
               XDG_STATE_HOME=str(home / '.local/state'), XDG_CACHE_HOME=str(cache),
               TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary),
               PYTHONNOUSERSITE='1', OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4',
               QT_QPA_PLATFORM='xcb', XDG_SESSION_TYPE='x11', GDK_BACKEND='x11')
    if not gpu:
        env.update(LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe',
                   __GLX_VENDOR_LIBRARY_NAME='mesa', LP_NUM_THREADS='4')
    else:
        for key in ('LIBGL_ALWAYS_SOFTWARE', 'GALLIUM_DRIVER', '__GLX_VENDOR_LIBRARY_NAME'):
            env.pop(key, None)
    return env


def main(args):
    # Validate BEFORE directory creation, app lookup, or executable access.
    root = storage()
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if args == ['workspace']:
        os.chdir(root)
        os.execv('/usr/bin/xdg-open', ['xdg-open', str(root)])
    if args == ['check']:
        print('PASS: expected writable AI Store and workspace verified')
        return
    if len(args) == 2 and args[0] == 'new-project':
        name = args[1]
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', name):
            raise ValueError('Use a lowercase project slug, digits and hyphens only')
        project = root / 'projects' / name
        if project.exists():
            raise ValueError('Project already exists; refusing to replace it')
        for directory in PROJECT_DIRS:
            contained(root, 'projects/' + name + '/' + directory, create=True)
        print(project)
        return
    gpu = bool(args and args[0] == '--gpu')
    if gpu:
        args = args[1:]
    if not args:
        raise ValueError('Usage: edsys-3d check | new-project SLUG | [--gpu] APP [ARG ...]')
    app, *arguments = args
    applications = json.loads((CONFIG / 'apps.json').read_text())
    if app not in applications:
        raise ValueError('Unknown 3D application')
    record = applications[app]
    executable = contained(root, record['executable'])
    env = environment(root, app, gpu)
    # A cwd on the filesystem also prevents an ordinary unmount while in use.
    os.chdir(contained(root, 'temporary/' + app))
    os.execve(str(executable), [str(executable), *record.get('arguments', []), *arguments], env)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as exc:
        print('3D launch refused: ' + str(exc), file=sys.stderr)
        sys.exit(1)
