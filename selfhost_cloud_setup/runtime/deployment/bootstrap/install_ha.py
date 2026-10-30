#!/usr/bin/env python3
"""Install into a fresh, stopped HA configuration using a preserved original.

Run only in a trusted environment with exclusive write access to the HA config.
This tool does not stop, start or restart Home Assistant.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import secrets
import tempfile
import yaml

from stage_ha import read_file, stage, put
from wizard import KNOWN
from build_haos_bundle import _publish_directory


def configuration(data):
    try:
        text = data.decode('utf-8')
        documents = list(yaml.compose_all(text, Loader=yaml.BaseLoader))
    except (UnicodeError, yaml.YAMLError, RecursionError) as e:
        raise ValueError('Invalid HA configuration YAML') from e
    if len(documents) != 1 or not isinstance(documents[0], yaml.MappingNode):
        raise ValueError('HA configuration must be a single mapping')
    keys = []
    for key, _ in documents[0].value:
        if not isinstance(key, yaml.ScalarNode) or key.value in ('<<', 'cloud'):
            raise ValueError('Existing Cloud or merged/complex root configuration is not supported')
        keys.append(key.value)
    if len(set(keys)) != len(keys): raise ValueError('Duplicate HA configuration key')
    return text


def cloud_state_exists(config):
    # Core creates an empty .cloud directory before any account is connected.
    directory=Path(config)/'.cloud'
    if directory.is_symlink():return True
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):return True
    state=Path(config)/'.storage/cloud'
    return state.exists() or state.is_symlink()


def install(config, package, candidate, expected_version):
    config = Path(config).absolute()
    if config.is_symlink() or not config.is_dir(): raise ValueError('Invalid HA configuration directory')
    if expected_version not in KNOWN: raise ValueError('Unsupported HA Core version')
    if read_file(config / '.HA_VERSION', 128).decode('ascii').strip() != expected_version:
        raise ValueError('HA Core version does not match the tested candidate')
    if cloud_state_exists(config):raise ValueError('Existing Cloud state detected')
    for rel in ('custom_components/cloud', 'selfhost'):
        p = config / rel
        if p.exists() or p.is_symlink(): raise ValueError('Existing Cloud state or destination detected')
    for rel in ('custom_components', '.storage'):
        p = config / rel
        if p.is_symlink() or (p.exists() and not p.is_dir()): raise ValueError('Unsafe configuration directory')
    path = config / 'configuration.yaml'
    original = read_file(path, 1024 * 1024)
    text = configuration(original)
    before = path.stat(follow_symlinks=False)
    lock = config / '.selfhost-fresh-install-lock'
    if lock.exists() or lock.is_symlink():
        if (lock.is_symlink() or not (lock/'RECOVERED.txt').is_file()
                or read_file(lock/'configuration.yaml.original') != original):
            raise ValueError('Existing installation record requires review')
        _publish_directory(lock,config/('.selfhost-recovered-'+secrets.token_hex(8)))
    with tempfile.TemporaryDirectory(prefix='.selfhost-stage-', dir=config) as tmp:
        staged = Path(tmp) / 'new'
        stage(staged, candidate, KNOWN[expected_version], package, expected_version)
        fragment = read_file(staged / 'configuration.yaml').decode('utf-8').removeprefix('default_config:\n')
        new = (text.rstrip() + '\n\n' + fragment).encode('utf-8')
        try:
            parsed = list(yaml.compose_all(new.decode('utf-8'), Loader=yaml.BaseLoader))
        except yaml.YAMLError as e: raise ValueError('Cannot safely append Cloud configuration') from e
        if len(parsed) != 1 or not isinstance(parsed[0], yaml.MappingNode):
            raise ValueError('Cannot safely append Cloud configuration')
        keys = [k.value for k, _ in parsed[0].value if isinstance(k, yaml.ScalarNode)]
        if keys.count('cloud') != 1: raise ValueError('Cannot safely append Cloud configuration')
        # Publish a complete recovery record before any Cloud data. A failed
        # journal write stays private in staging and cannot strand an empty lock.
        journal = Path(tmp) / 'journal'
        journal.mkdir(mode=0o700)
        put(journal / 'configuration.yaml.original', original)
        file_hashes = {}
        for directory in ('custom_components/cloud', 'selfhost'):
            for file in sorted((staged / directory).rglob('*')):
                if file.is_file():
                    file_hashes[file.relative_to(staged).as_posix()] = hashlib.sha256(read_file(file)).hexdigest()
        put(journal / 'installation.json', (json.dumps({
            'schema': 1, 'original_sha256': hashlib.sha256(original).hexdigest(),
            'installed_sha256': hashlib.sha256(new).hexdigest(),
            'files': file_hashes}, sort_keys=True) + '\n').encode())
        put(journal / 'RECOVERY.txt', b'Installation may be partial. Preserve this directory and inspect all changes before retrying.\n')
        _publish_directory(journal, lock)
        custom = config / 'custom_components'
        custom.mkdir(mode=0o700, exist_ok=True)
        _publish_directory(staged / 'custom_components/cloud', custom / 'cloud')
        _publish_directory(staged / 'selfhost', config / 'selfhost')
        fd, name = tempfile.mkstemp(prefix='.selfhost-configuration-', dir=config)
        try:
            os.fchmod(fd, stat.S_IMODE(before.st_mode))
            with os.fdopen(fd, 'wb') as f:
                f.write(new); f.flush(); os.fsync(f.fileno())
            now = path.stat(follow_symlinks=False)
            if (now.st_dev, now.st_ino, now.st_mode, now.st_uid, now.st_gid) != (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid) or read_file(path, 1024 * 1024) != original:
                raise ValueError('Configuration changed; original and staged files preserved for recovery')
            # The caller must ensure no other writer; the comparison and rename
            # cannot provide compare-and-swap semantics to uncooperative writers.
            if (now.st_uid, now.st_gid) != (os.geteuid(), os.getegid()):
                os.chown(name, now.st_uid, now.st_gid)
            os.replace(name, path)
        finally:
            if os.path.exists(name): os.unlink(name)
    put(lock / 'COMPLETE.txt', b'Files installed. Core has not been started or verified.\n')
    return lock


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--core-version', required=True, choices=sorted(KNOWN))
    p.add_argument('--candidate', type=Path)
    p.add_argument('--core-stopped-and-exclusive', action='store_true')
    a = p.parse_args()
    if not a.core_stopped_and_exclusive: p.error('Core must be stopped and no other process may write this configuration')
    candidate = a.candidate or Path(__file__).resolve().parent / 'candidates' / ('core-' + a.core_version) / 'cloud'
    try: lock = install(a.config, a.package, candidate, a.core_version)
    except (OSError, ValueError, UnicodeError) as e: p.exit(1, f'Install stopped: {e}. Preserve any installation lock and original.\n')
    print(f'Installed files; Core has not been started. Original and receipt: {lock}')

if __name__ == '__main__': main()
