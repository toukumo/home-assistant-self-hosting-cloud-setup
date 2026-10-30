#!/usr/bin/env python3
"""Export only public HA connection material, never backend account/private keys."""
import argparse
import base64
import errno
import json
import os
from pathlib import Path
import tempfile

from stage_ha import read_file, parse_object, put
from build_haos_bundle import _publish_directory

FILES=('ca.pem','jwt-public.pem','selfhost.json','configuration-fragment.yaml')


def publish_export(path, data):
    """Publish a private 0600 export without replacing an existing name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.selfhost-connection-', dir=path.parent)
    original = os.fstat(fd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes only the fully written file and never replaces
        # an existing export, including one created by a concurrent request.
        try:
            os.link(temporary, path)
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP,
                                   errno.ENOSYS, errno.EMLINK):
                raise
            # Some filesystems cannot create hard links. The existing helper
            # uses an exclusive rename, not a replacing rename or partial copy.
            _publish_directory(temporary, path)
    finally:
        try:
            current = os.lstat(temporary)
            if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                os.unlink(temporary)
        except OSError:
            # Never replace the primary result with a cleanup error.
            pass


def export(package):
    root=Path(package)/'home-assistant'
    if root.is_symlink() or not root.is_dir():raise ValueError('Invalid connection directory')
    return {'schema':1,'files':{name:base64.b64encode(read_file(root/name,8192)).decode('ascii') for name in FILES}}


def unpack(data,directory):
    obj=parse_object(data)
    if set(obj)!={'schema','files'} or obj['schema']!=1 or not isinstance(obj['files'],dict) or set(obj['files'])!=set(FILES):
        raise ValueError('Invalid public connection file')
    decoded={}
    for name,value in obj['files'].items():
        if not isinstance(value,str) or len(value)>12000:raise ValueError('Invalid connection data')
        try:decoded[name]=base64.b64decode(value,validate=True)
        except ValueError:raise ValueError('Invalid connection encoding') from None
        if len(decoded[name])>8192:raise ValueError('Connection data too large')
    root=Path(directory)/'home-assistant';root.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,value in decoded.items():put(root/name,value)
    return Path(directory)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    try:publish_export(a.output,(json.dumps(export(a.package),indent=2)+'\n').encode())
    except (OSError,ValueError) as e:p.exit(1,f'Connection export failed: {e}\n')
    print(f'Public connection file exported to {a.output}')

if __name__=='__main__':main()
