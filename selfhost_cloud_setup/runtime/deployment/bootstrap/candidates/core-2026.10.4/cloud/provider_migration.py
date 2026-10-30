"""Explicit, resumable owner transition; backup private prefs before any writes."""
import asyncio
import copy
import contextvars
import errno
import json
import logging
import os
import re
import stat
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


def validate_generated_hook(details, base):
    key = details.get('cloudhook_id')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', key):
        raise ValueError('Invalid generated cloudhook')
    if details.get('url') != base.rstrip('/') + '/hook/' + key:
        raise ValueError('Cloudhook URL does not match configured provider')
    return details


def filter_hook_registration(payload, hooks, base):
    allowed = set()
    for hook in hooks.values():
        if not isinstance(hook, dict):
            continue
        try:
            validate_generated_hook({'url': hook.get('cloudhook_url'), 'cloudhook_id': hook.get('cloudhook_id')}, base)
            allowed.add(hook['cloudhook_id'])
        except ValueError:
            continue
    return {**payload, 'cloudhook_ids': [key for key in payload.get('cloudhook_ids', []) if isinstance(key, str) and key in allowed]}


def _same_staging_file(path, original):
    if original is None:
        return False
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(current.st_mode)
            and (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino))


def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = None
    original_staging = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            original_staging = os.fstat(file.fileno())
            os.fchmod(file.fileno(), 0o600)
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        if not _same_staging_file(temporary, original_staging):
            raise OSError("Migration checkpoint staging changed")
        temporary.replace(path)
    finally:
        if temporary:
            try:
                if _same_staging_file(temporary, original_staging):
                    temporary.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("Cannot remove migration checkpoint staging file")


def _validate_checkpoint(data):
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
            or not isinstance(data.get('username'), str) or not data['username']
            or not isinstance(data.get('base'), str) or not data['base']):
        raise ValueError('Invalid migration checkpoint')
    try:
        parsed = urlsplit(data['base'])
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError()
    except ValueError:
        raise ValueError('Invalid migration checkpoint') from None
    original, pending = data.get('original'), data.get('pending')
    if (not isinstance(original, dict) or not isinstance(original.get('username'), str)
            or not original['username']):
        raise ValueError('Invalid migration checkpoint')
    hooks = original.get('cloudhooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Invalid migration checkpoint')
    for webhook_id, hook in hooks.items():
        if (not isinstance(webhook_id, str) or not webhook_id or not isinstance(hook, dict)
                or hook.get('webhook_id') != webhook_id
                or ('managed' in hook and type(hook['managed']) is not bool)):
            raise ValueError('Invalid migration checkpoint')
    if (not isinstance(pending, list)
            or any(not isinstance(key, str) or not key or key not in hooks for key in pending)
            or len(set(pending)) != len(pending)):
        raise ValueError('Invalid migration checkpoint')


def _read_private(path):
    if not hasattr(os, 'O_NONBLOCK') or not hasattr(os, 'O_NOFOLLOW'):
        raise OSError(errno.ENOTSUP, 'Safe checkpoint reads are unavailable')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Migration checkpoint must be a regular file')
        with os.fdopen(fd, 'r', encoding='utf-8', closefd=False) as file:
            data = json.load(file)
        _validate_checkpoint(data)
        return data
    finally:
        os.close(fd)


class ProviderMigration:
    def __init__(self, prefs, directory):
        self.prefs = prefs
        self.path = Path(directory) / 'migration.json'
        self.original = copy.deepcopy(prefs._prefs)
        self.lock = asyncio.Lock()

    async def _checkpoint(self, data):
        # The executor keeps writing after caller cancellation. Keep the owning
        # transition lock until this admitted write finishes, then propagate it.
        def invoke():
            # Keep storage errors out of cancelled shield diagnostics.
            try:
                _write_private(self.path, data)
            except Exception as error:
                return error
            return None
        context = contextvars.copy_context()
        write = asyncio.get_running_loop().run_in_executor(None, context.run, invoke)
        cancellation = None
        while not write.done():
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError as error:
                cancellation = error
            except Exception:
                break
        try:
            error = write.result()
            if error is not None:
                raise error
        except Exception:
            if cancellation is not None:
                raise cancellation
            raise
        if cancellation is not None:
            raise cancellation

    async def prepare(self, username, base, remote_domain):
        async with self.lock:
            parsed = urlsplit(base)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Migration requires a configured HTTPS provider')
            base = base.rstrip('/')
            data = await asyncio.to_thread(_read_private, self.path)
            current = self.prefs._prefs
            if data is not None:
                if data.get('version') != 1 or data.get('username') != username or data.get('base') != base:
                    raise ValueError('Existing migration belongs to a different provider')
                if current.get('username') not in (username, data['original'].get('username')):
                    raise ValueError('Current provider identity changed since backup')
            else:
                if not current.get('username') or current['username'] == username:
                    return
                original = self.original if self.original.get('username') == current['username'] else copy.deepcopy(current)
                hooks = original.get('cloudhooks', {})
                pending = []
                for webhook_id, hook in hooks.items():
                    if not isinstance(hook, dict) or hook.get('webhook_id') != webhook_id:
                        raise ValueError('Invalid legacy cloudhook association')
                    try:
                        validate_generated_hook({'url': hook.get('cloudhook_url'), 'cloudhook_id': hook.get('cloudhook_id')}, base)
                    except ValueError:
                        pending.append(webhook_id)
                data = {'version': 1, 'username': username, 'base': base,
                        'original': copy.deepcopy(original), 'pending': pending}
                _validate_checkpoint(data)
                await self._checkpoint(data)
            if current.get('username') != username:
                # Keep local choices; clear only provider-bound identity/link metadata.
                retained = {k: v for k, v in current.get('cloudhooks', {}).items() if k not in data['pending']}
                await self.prefs._save_prefs({**current, 'username': username, 'cloud_user': None,
                    'google_connected': False, 'remote_domain': remote_domain, 'cloudhooks': retained})

    async def restore_hooks(self, cloud):
        async with self.lock:
            data = await asyncio.to_thread(_read_private, self.path)
            if data is None:
                return
            if data['username'] != cloud.username:
                raise ValueError('Migration owner does not match active provider')
            for webhook_id in list(data['pending']):
                hook = self.prefs.cloudhooks.get(webhook_id)
                if hook:
                    validate_generated_hook({'url': hook.get('cloudhook_url'), 'cloudhook_id': hook.get('cloudhook_id')}, data['base'])
                else:
                    old = data['original']['cloudhooks'][webhook_id]
                    hook = await cloud.cloudhooks.async_create(webhook_id, old.get('managed', False))
                    validate_generated_hook({'url': hook['cloudhook_url'], 'cloudhook_id': hook['cloudhook_id']}, data['base'])
                data['pending'].remove(webhook_id)
                await self._checkpoint(data)
