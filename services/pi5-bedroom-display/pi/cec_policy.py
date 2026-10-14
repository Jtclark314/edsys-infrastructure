#!/usr/bin/env python3
"""Disable Kodi's CEC default without changing other peripheral definitions."""
from pathlib import Path
import re
import shutil


def policy(text):
    pattern=r'(<peripheral\b[^>]*\bbus="cec"[^>]*\bmapTo="cec"[^>]*>)(.*?)(</peripheral>)'
    matches=list(re.finditer(pattern,text,re.S))
    if len(matches)!=1:
        raise ValueError('Expected exactly one native CEC peripheral definition')
    match=matches[0]
    body=match.group(2)
    for key in ['enabled','activate_source']:
        body,count=re.subn(r'(key="'+key+r'"[^>]*\bvalue=")[^"]*(")',r'\g<1>0\2',body)
        if count!=1:raise ValueError('Unexpected CEC setting definition')
    return text[:match.start(2)]+body+text[match.end(2):]


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-dir',type=Path,required=True)
    args=parser.parse_args()
    p=Path('/usr/share/kodi/system/peripherals.xml')
    before=p.read_text(encoding='utf-8');after=policy(before)
    if before!=after:
        args.backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup=args.backup_dir/'peripherals.xml'
        if backup.exists():raise RuntimeError('CEC backup already exists; inspect before changing')
        shutil.copy2(p,backup)
        p.write_text(after,encoding='utf-8')
    print('CEC default disabled; other peripheral definitions preserved.')
