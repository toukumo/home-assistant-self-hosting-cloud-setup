#!/usr/bin/env python3
"""Prepare fresh Cloud and HA files together using a known-tested candidate.

Does not start or install services. Output must be a new, private location.
"""
import argparse
import getpass
import os
from pathlib import Path
import sys
import tempfile

from prepare import prepare
from connection_file import export as export_connection
from stage_ha import put
import json
from stage_ha import collect, stage
from build_haos_bundle import _publish_directory

# This identifies the exact Core 2026.10.4 candidate; qualification is recorded
# separately so a source hash never implies a native-runtime test.
# It is not a general Core compatibility or production-release guarantee.
KNOWN = {'2026.10.4': '211f6c4b608616dd58e5bb7f1beb77ec397442d1e6c74f73811ca778ce77e49a'}


def assemble(output, candidate, core_version, api, remote, email, password, voice=None):
    expected = KNOWN.get(core_version)
    if not expected: raise ValueError('No tested candidate is available for this Core version')
    if collect(candidate)[1] != expected: raise ValueError('Candidate does not match the tested version')
    destination = Path(output).absolute()
    if destination.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError('Choose a private destination outside the source repository')
    if destination.exists() or destination.is_symlink(): raise FileExistsError('Destination already exists')
    if destination.parent.is_symlink() or not destination.parent.is_dir(): raise ValueError('Invalid output parent')
    with tempfile.TemporaryDirectory(prefix='.ha-cloud-wizard-', dir=destination.parent) as t:
        root = Path(t) / 'setup'; root.mkdir(mode=0o700)
        prepare(root / 'cloud-server', api, remote, email, password, voice=voice)
        stage(root / 'new-ha', candidate, expected, root / 'cloud-server', core_version)
        put(root / 'ha-connection.json', (json.dumps(export_connection(root / 'cloud-server'), indent=2) + '\n').encode())
        _publish_directory(root, destination)
    return destination


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, help='Optional reviewed candidate directory; defaults to the bundled candidate')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--core-version', required=True, choices=sorted(KNOWN))
    a=p.parse_args()
    candidate = a.candidate or Path(__file__).resolve().parent / 'candidates' / ('core-' + a.core_version) / 'cloud'
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        p.exit(1, 'A private interactive terminal is required.\n')
    try:
        api=input('Cloud API hostname: ').strip()
        remote=input('Home Assistant remote hostname: ').strip()
        email=input('Cloud account email: ').strip()
        password=getpass.getpass('Cloud account password: ')
        if password!=getpass.getpass('Confirm password: '): raise ValueError('Passwords do not match')
        output=assemble(a.output,candidate,a.core_version,api,remote,email,password)
    except (ValueError,OSError) as e:
        p.exit(1, f'Setup preparation failed: {e}\n')
    print(f'Prepared {output}. No service was installed or started.')
    print('Public DNS, public certificates, deployment and final qualification are still required.')

if __name__ == '__main__': main()
