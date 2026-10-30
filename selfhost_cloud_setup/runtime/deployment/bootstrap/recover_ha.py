#!/usr/bin/env python3
"""Recover a fresh installation without deleting code, config or Cloud state.

Requires stopped Core and exclusive access. Refuses changed files or Cloud
login state; preserves installed directories inside the original journal.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from stage_ha import read_file, parse_object, put
from install_ha import cloud_state_exists
from build_haos_bundle import _publish_directory


def digest(data): return hashlib.sha256(data).hexdigest()


def recover(config):
    config = Path(config).absolute()
    if config.is_symlink() or not config.is_dir(): raise ValueError('Invalid configuration directory')
    if (config/'custom_components').is_symlink():raise ValueError('Unsafe component directory')
    lock = config / '.selfhost-fresh-install-lock'
    if lock.is_symlink() or not lock.is_dir(): raise ValueError('Installation recovery record unavailable')
    if cloud_state_exists(config):raise ValueError('Cloud state exists; preserve it for reviewed recovery')
    if (lock/'RECOVERED.txt').exists(): raise ValueError('Installation has already been recovered')
    original = read_file(lock/'configuration.yaml.original')
    journal = parse_object(read_file(lock/'installation.json', 128*1024))
    if journal.get('schema') != 1 or digest(original) != journal.get('original_sha256'):
        raise ValueError('Original configuration does not match recovery record')
    expected = journal.get('files')
    if not isinstance(expected, dict) or not expected or len(expected)>512:
        raise ValueError('Invalid recovery record')
    for name, value in expected.items():
        if not isinstance(name,str):raise ValueError('Unsafe recovery record')
        parts=Path(name).parts
        if (not isinstance(name,str) or not isinstance(value,str) or len(value)!=64
                or Path(name).is_absolute() or '..' in parts or '.' in parts
                or not (name.startswith('custom_components/cloud/') or name.startswith('selfhost/'))):
            raise ValueError('Unsafe recovery record')
    path=config/'configuration.yaml'
    current=read_file(path)
    if digest(current) not in (journal.get('original_sha256'), journal.get('installed_sha256')):
        raise ValueError('Configuration changed after installation; nothing recovered')
    roots=('custom_components/cloud','selfhost')
    # Verify all targets before changing any. Bytecode is retained with its code.
    for rel in roots:
        directory=config/rel
        retained=lock/('retained-'+directory.name)
        if directory.is_symlink(): raise ValueError('Unsafe installed directory')
        if retained.is_symlink(): raise ValueError('Unsafe retained directory')
        if directory.exists() and retained.exists(): raise ValueError('Recovery destination exists')
        candidate=directory if directory.exists() else retained
        if not candidate.exists():
            # Installed YAML is published only after both trees. Missing one
            # in that state means recovery cannot preserve what it describes.
            if current != original:
                raise ValueError('Installed directory is missing; nothing recovered')
            continue  # Partial installation may not have published this root.
        if not candidate.is_dir(): raise ValueError('Unsafe installed directory')
        for file in candidate.rglob('*'):
            if file.is_symlink(): raise ValueError('Unsafe installed file')
            if file.is_dir(): continue
            relative=file.relative_to(candidate)
            name=rel+'/'+relative.as_posix()
            data=read_file(file)
            if '__pycache__' in relative.parts and file.suffix=='.pyc': continue
            if digest(data)!=expected.get(name): raise ValueError('Installed files changed; nothing recovered')
        for name,value in expected.items():
            if name.startswith(rel+'/'):
                file=candidate/name[len(rel)+1:]
                if not file.exists() or digest(read_file(file))!=value:
                    raise ValueError('Installed files changed; nothing recovered')
    # An interrupted write can leave a truncated exclusive-create file.
    # Validate any existing copy before moving directories or replacing YAML.
    saved=lock/'configuration.yaml.before-recovery'
    try:
        saved.lstat()
    except FileNotFoundError:
        put(saved,current)
    else:
        if digest(read_file(saved)) not in (journal.get('original_sha256'), journal.get('installed_sha256')):
            raise ValueError('Saved configuration is incomplete; nothing recovered')
    for rel in roots:
        directory=config/rel
        if directory.exists(): _publish_directory(directory,lock/('retained-'+directory.name))
    if current != original:
        before=path.stat(follow_symlinks=False)
        fd,name=tempfile.mkstemp(prefix='.selfhost-recovery-',dir=config)
        try:
            os.fchmod(fd,stat.S_IMODE(before.st_mode))
            with os.fdopen(fd,'wb') as stream: stream.write(original);stream.flush();os.fsync(stream.fileno())
            if read_file(path)!=current: raise ValueError('Configuration changed during recovery; preserve journal')
            if (before.st_uid,before.st_gid)!=(os.geteuid(),os.getegid()):os.chown(name,before.st_uid,before.st_gid)
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    put(lock/'RECOVERED.txt',b'Original configuration restored. Installed code and public connection files retained.\n')
    return lock


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--core-stopped-and-exclusive',action='store_true')
    a=p.parse_args()
    if not a.core_stopped_and_exclusive:p.error('Core must be stopped and no other writer may modify the configuration')
    try: path=recover(a.config)
    except (OSError,ValueError) as e:p.exit(1,f'Recovery stopped: {e}\n')
    print(f'Restored configuration; retained installation files: {path}. Core has not been started.')

if __name__=='__main__':main()
