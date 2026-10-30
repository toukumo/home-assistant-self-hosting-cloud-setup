#!/usr/bin/env python3
"""Stage a new HA config directory from a reviewed Cloud candidate and setup package.

Never merges into an existing HA config and never starts Home Assistant.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from build_haos_bundle import _publish_directory

MAX_FILES = 512
MAX_BYTES = 8 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError('Invalid JSON constant')


def parse_object(data):
    try:
        result = json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid JSON') from error
    if not isinstance(result, dict): raise ValueError('Expected a JSON object')
    return result


def collect(candidate):
    base = Path(candidate)
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Candidate must be a regular directory')
    entries = []; total = 0
    for path in sorted(base.rglob('*')):
        rel = path.relative_to(base)
        meta = path.lstat()
        if stat.S_ISLNK(meta.st_mode): raise ValueError('Candidate contains a symbolic link')
        if stat.S_ISDIR(meta.st_mode): continue
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise ValueError('Candidate contains an unsafe file')
        if any(part.startswith('.') for part in rel.parts) or rel.name in ('selfhost.json', 'account.json'):
            raise ValueError('Candidate contains private or hidden data')
        if meta.st_size > MAX_BYTES or total + meta.st_size > MAX_BYTES or len(entries) >= MAX_FILES:
            raise ValueError('Candidate is too large')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            current = os.fstat(fd)
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or (current.st_dev, current.st_ino) != (meta.st_dev, meta.st_ino):
                raise ValueError('Candidate changed while reading')
            with os.fdopen(fd, 'rb', closefd=False) as f: data = f.read(MAX_BYTES + 1)
        finally: os.close(fd)
        if len(data) != meta.st_size: raise ValueError('Candidate changed while reading')
        total += len(data); entries.append((rel.as_posix(), data))
    names = {name for name, _ in entries}
    if not {'manifest.json', '__init__.py', 'local_auth.py', 'provider_ui.py'} <= names:
        raise ValueError('Candidate is incomplete')
    manifest = parse_object(dict(entries)['manifest.json'])
    if manifest.get('domain') != 'cloud': raise ValueError('Candidate is not the Cloud integration')
    digest = hashlib.sha256()
    for name, data in entries:
        digest.update(name.encode('utf-8') + b'\0' + len(data).to_bytes(8, 'big') + data)
    return entries, digest.hexdigest()


def read_file(path, limit=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
            raise ValueError('Expected a bounded regular setup file')
        with os.fdopen(fd, 'rb', closefd=False) as f: data = f.read(limit + 1)
        if len(data) != st.st_size: raise ValueError('Setup file changed while reading')
        return data
    finally: os.close(fd)


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())


def stage(output, candidate, expected_sha256, package, core_version):
    if not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d+', core_version):
        raise ValueError('Specify an exact stable HA Core version')
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('Specify the reviewed candidate SHA256')
    output = Path(output).absolute()
    if output.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError('Choose a destination outside the source repository')
    if output.exists() or output.is_symlink(): raise FileExistsError('Destination already exists')
    if output.parent.is_symlink() or not output.parent.is_dir(): raise ValueError('Invalid output parent')
    package = Path(package)
    if package.is_symlink() or not package.is_dir(): raise ValueError('Invalid setup package')
    ha = package / 'home-assistant'
    if ha.is_symlink() or not ha.is_dir(): raise ValueError('Invalid HA setup directory')
    entries, actual = collect(candidate)
    if actual != expected_sha256: raise ValueError('Candidate SHA256 does not match review')
    ca = read_file(ha / 'ca.pem'); jwt = read_file(ha / 'jwt-public.pem')
    settings = read_file(ha / 'selfhost.json'); fragment = read_file(ha / 'configuration-fragment.yaml')
    info = parse_object(settings)
    base_keys = {'url', 'ca', 'jwt_public', 'snitun_websocket', 'certificate_issuer', 'migrate_existing_account'}
    if (set(info) not in (base_keys, base_keys | {'local_voice'})
            or info.get('migrate_existing_account') is not False or info.get('certificate_issuer') is not True
            or info.get('ca') != '/config/selfhost/ca.pem' or info.get('jwt_public') != '/config/selfhost/jwt-public.pem'):
        raise ValueError('Invalid fresh-install configuration')
    if 'local_voice' in info:
        from voice_probe import endpoint
        voice=info['local_voice']
        if not isinstance(voice,dict) or set(voice) not in ({'stt','tts'}, {'stt','tts','tts_ja'}):
            raise ValueError('Invalid voice configuration')
        for value in voice.values():endpoint(value)
    cert = x509.load_pem_x509_certificate(ca)
    try:
        is_ca = cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        raise ValueError('Expected a CA certificate') from None
    if not is_ca:
        raise ValueError('Expected a CA certificate')
    public = serialization.load_pem_public_key(jwt)
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 2048:
        raise ValueError('Expected a 2048-bit or larger RSA public key')
    # Refuse package substitution or handwritten extra YAML; the canonical
    # fragment is reconstructed from a validated HTTPS hostname.
    from prepare import hostname
    if not isinstance(info.get('url'), str): raise ValueError('Invalid backend URL')
    host = info['url'].removeprefix('https://')
    if info.get('url') != 'https://' + hostname(host) or info.get('snitun_websocket') != 'wss://' + host + '/snitun':
        raise ValueError('Invalid backend URL')
    canonical = ('cloud:\n  mode: development\n' + ''.join('  ' + field + ': ' + host + '\n' for field in
        ('api_server', 'relayer_server', 'account_link_server', 'remotestate_server', 'acme_server'))).encode()
    if fragment != canonical: raise ValueError('Configuration fragment differs from prepared setup')
    with tempfile.TemporaryDirectory(prefix='.ha-cloud-new-ha-', dir=output.parent) as t:
        root = Path(t) / 'config'; root.mkdir(mode=0o700)
        for name, data in entries: put(root / 'custom_components' / 'cloud' / name, data)
        put(root / 'custom_components/cloud/selfhost.json', settings)
        put(root / 'selfhost/ca.pem', ca); put(root / 'selfhost/jwt-public.pem', jwt)
        put(root / 'configuration.yaml', b'default_config:\n' + fragment)
        put(root / 'bootstrap-metadata.json', (json.dumps({'core_version': core_version, 'candidate_sha256': actual,
            'status': 'staged_not_installed_or_qualified'}, indent=2) + '\n').encode())
        _publish_directory(root, output)
    return actual


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--candidate-sha256', required=True)
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--core-version', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try: stage(a.output, a.candidate, a.candidate_sha256, a.package, a.core_version)
    except (ValueError, OSError, json.JSONDecodeError) as e: p.exit(1, f'Staging failed: {e}\n')
    print(f'New HA configuration staged at {a.output}; compatibility and installation remain required.')

if __name__ == '__main__': main()
