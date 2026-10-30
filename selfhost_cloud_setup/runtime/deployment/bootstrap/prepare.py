#!/usr/bin/env python3
"""Prepare credentials and configuration for a new, separate Cloud installation.

This does not install software, deploy services, or touch an existing HA instance.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from build_haos_bundle import _publish_directory
from voice_probe import endpoint as voice_endpoint

CADDY_IMAGE = 'caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d'


def proxy_configuration(api, remote):
    """Render the two public routes; verify both internal TLS peers."""
    return (f'{api} {{\n'
            '    reverse_proxy https://backend:8443 {\n'
            '        transport http {\n'
            '            tls_trust_pool file /etc/caddy/backend-ca.pem\n'
            '            tls_server_name cloud.test\n'
            '        }\n'
            '    }\n'
            '}\n\n'
            f'{remote} {{\n'
            '    reverse_proxy https://backend:443 {\n'
            '        header_up -X-Forwarded-For\n'
            '        header_up -X-Forwarded-Proto\n'
            '        header_up -X-Forwarded-Host\n'
            '        transport http {\n'
            '            tls_trust_pool file /etc/caddy/backend-ca.pem\n'
            f'            tls_server_name {remote}\n'
            '        }\n'
            '    }\n'
            '}\n')


def compose_configuration(api, remote, voice_enabled=False):
    """Compose JSON is valid YAML; bind only the private CA to the proxy."""
    environment = {'CLOUD_DATA': '/data', 'CLOUD_BASE_URL': 'https://' + api,
                   'CLOUD_REMOTE_DOMAIN': remote, 'CLOUD_RELAY_HOST': api,
                   'CLOUD_TRUSTED_PROXIES': 'caddy'}
    if voice_enabled: environment['CLOUD_VOICE_RELAY'] = '1'
    return {'services': {
        'backend': {
            'image': 'ha-cloud-selfhost:local',
            'restart': 'unless-stopped',
            'user': '${CLOUD_UID:?Use start_server.py}:${CLOUD_GID:?Use start_server.py}',
            'environment': environment,
            'volumes': ['./backend:/data'],
            'depends_on': ['caddy'],
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        },
        'caddy': {
            'image': CADDY_IMAGE, 'restart': 'unless-stopped',
            'ports': ['80:80', '443:443'],
            'volumes': ['./Caddyfile:/etc/caddy/Caddyfile:ro',
                        './home-assistant/ca.pem:/etc/caddy/backend-ca.pem:ro',
                        'caddy_data:/data', 'caddy_config:/config'],
        },
    }, 'volumes': {'caddy_data': {}, 'caddy_config': {}}}


def hostname(value):
    if not isinstance(value, str) or not value.isascii() or len(value) > 253 or value != value.lower():
        raise ValueError('Use a lowercase public DNS hostname')
    labels = value.split('.')
    if len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('Use a lowercase public DNS hostname')
    if labels[-1] in {'test', 'local', 'localhost', 'invalid', 'example'} or labels[-1].isdigit():
        raise ValueError('Use a public DNS hostname, not a test name or IP')
    return value


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))
        f.flush()
        os.fsync(f.fileno())


def private(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def prepare(output, api, remote, email, password, voice=None):
    api, remote = hostname(api), hostname(remote)
    if voice is not None:
        if not isinstance(voice, dict) or set(voice) not in ({'stt','tts'}, {'stt','tts','tts_ja'}):
            raise ValueError('Select both STT and TTS services')
        for value in voice.values(): voice_endpoint(value)
    if api == remote:
        raise ValueError('API and remote UI hostnames must be distinct')
    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+', email):
        raise ValueError('Enter a valid account email')
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Password must contain 12 to 256 characters')
    try:
        password_bytes = password.encode('utf-8')
    except UnicodeError:
        raise ValueError('Password must be valid Unicode') from None
    destination = Path(output).absolute()
    repository = Path(__file__).resolve().parents[2]
    if destination.resolve().is_relative_to(repository):
        raise ValueError('Choose a private destination outside the source repository')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Destination already exists; nothing was replaced')
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError('Destination parent must be an existing directory')
    base = 'https://' + api
    with tempfile.TemporaryDirectory(prefix='.ha-cloud-bootstrap-', dir=destination.parent) as t:
        root = Path(t) / 'package'; root.mkdir(mode=0o700)
        backend = root / 'backend'; backend.mkdir(mode=0o700)
        ha = root / 'home-assistant'; ha.mkdir(mode=0o700)
        now = datetime.now(timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Self-hosted Cloud private CA')])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        api_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        api_cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'cloud.test')]))
                    .issuer_name(ca_name).public_key(api_key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=90))
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                    .add_extension(x509.SubjectKeyIdentifier.from_public_key(api_key.public_key()), critical=False)
                    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                    .add_extension(x509.SubjectAlternativeName([x509.DNSName('cloud.test')]), critical=False)
                    .sign(ca_key, hashes.SHA256()))
        signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_pem = ca.public_bytes(serialization.Encoding.PEM)
        public = signing.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        salt = secrets.token_bytes(16)
        account = {'email': email, 'salt': salt.hex(),
                   'password_hash': hashlib.scrypt(password_bytes, salt=salt, n=16384, r=8, p=1).hex(),
                   'fernet_key': Fernet.generate_key().decode('ascii')}
        for name, contents in {'account.json': json.dumps(account), 'jwt-key.pem': private(signing),
                               'jwt-public.pem': public, 'ca.pem': ca_pem, 'ca-key.pem': private(ca_key),
                               'api-key.pem': private(api_key), 'api-cert.pem': api_cert.public_bytes(serialization.Encoding.PEM)}.items():
            write(backend / name, contents)
        write(ha / 'ca.pem', ca_pem); write(ha / 'jwt-public.pem', public)
        settings = {'url': base, 'ca': '/config/selfhost/ca.pem', 'jwt_public': '/config/selfhost/jwt-public.pem',
                    'snitun_websocket': 'wss://' + api + '/snitun', 'certificate_issuer': True,
                    'migrate_existing_account': False}
        if voice is not None: settings['local_voice'] = voice.copy()
        write(ha / 'selfhost.json', json.dumps(settings, indent=2) + '\n')
        fragment = ('cloud:\n  mode: development\n' + ''.join('  ' + field + ': ' + api + '\n' for field in
                    ('api_server', 'relayer_server', 'account_link_server', 'remotestate_server', 'acme_server')))
        write(ha / 'configuration-fragment.yaml', fragment)
        environment = {'CLOUD_BASE_URL': base, 'CLOUD_REMOTE_DOMAIN': remote, 'CLOUD_RELAY_HOST': api}
        if voice is not None: environment['CLOUD_VOICE_RELAY'] = '1'
        write(root / 'backend-environment.json', json.dumps(environment, indent=2) + '\n')
        write(root / 'Caddyfile', proxy_configuration(api, remote))
        write(root / 'compose.yaml', json.dumps(compose_configuration(api, remote, voice is not None), indent=2) + '\n')
        write(root / 'README.txt', 'Preparation only: no service has been installed or started.\nKeep backend private; HA files contain no account password or private signing key.\nPublic DNS, ports and a compatible HA integration must be configured separately.\n')
        _publish_directory(root, destination)
    return destination


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--api-host', required=True)
    p.add_argument('--remote-host', required=True)
    p.add_argument('--email', required=True)
    args = p.parse_args()
    try:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError('A private interactive terminal is required for the password')
        password = getpass.getpass('Cloud account password: ')
        if password != getpass.getpass('Confirm password: '):
            raise ValueError('Passwords do not match')
        path = prepare(args.output, args.api_host, args.remote_host, args.email, password)
    except (ValueError, OSError) as e:
        p.exit(1, f'Preparation failed: {e}\n')
    print(f'Prepared {path}. No services or HA settings were changed.')

if __name__ == '__main__':
    main()
